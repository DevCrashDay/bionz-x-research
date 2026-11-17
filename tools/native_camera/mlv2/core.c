/* Mod Loader v2 core: position-independent, loaded by the shim into mmap memory.
 *
 * - Builds the 7th "Mods" tab (same fake-node technique as v1).
 * - Rescans the mod folders every time the menu root is set; enabled mods keep their state.
 * - Loads SMOD v1 (fixed address) and SMOD v2 (relocatable, core-allocated memory) mods.
 * - Lifecycle: v2 on()/off() offsets; v1 flag bit 1 (Thumb entry at code_addr).
 * - Survives core upgrades: the context is versioned and adopted by the next core.
 * - Takes over a running v1 loader: imports its enabled mods, leaves its state and code intact.
 *
 * Rules for position independence: no writable globals, no static pointer tables; function
 * and string addresses are only taken at run time (PC-relative). build_wsl.sh rejects absolute
 * relocations.
 */
#include "mlv2.h"
#include "mlv2_sys.h"
#include "../mods_tab_look_layout.h"

#define CTX_MAGIC    0x58544332u  /* "2CTX" */
#define CTX_VERSION  2u
#define MAX_MODS     12u
#define MAX_PAGES    2u
#define KIND_V1      1u
#define KIND_V2      2u

typedef struct Node {
    void   **vtable;
    void   **children;
    int32_t  child_count;
    void    *props;
    void    *parent;
    uint32_t layer_depth;
    int32_t  selected_idx;
    uint32_t s1c, s20, s24;
} Node;

typedef struct {
    uint32_t target;
    uint8_t  size, fixup, pad[2];
    uint8_t  orig[SMOD_MAX_PATCH];
    uint8_t  patch[SMOD_MAX_PATCH];   /* v1: final bytes; v2: template (fixup applied at ON) */
} Hook;

typedef struct {
    char     name[32];
    char     path[64];
    uint32_t fnv;
    uint8_t  kind, enabled, invalid, flags;
    uint8_t  hook_count, present, stale, off_called;
    uint32_t code_size;
    uint32_t extra_size;   /* v1: state_size; v2: bss_size */
    uint32_t addr;         /* v1: fixed code_addr; v2: mmap base (0 until first ON) */
    uint32_t map_size;     /* v2: size of the mapping at addr */
    uint32_t state_addr;   /* v1 only */
    uint32_t on_off, off_off;
    uint32_t data_off;     /* file offset of the code bytes */
    Hook     hooks[SMOD_MAX_HOOKS];
} Mod;

typedef struct {
    uint32_t magic, version;
    uint32_t mod_count, page_count;
    uint32_t vtable_ready;
    Node    *orig_root;
    Node    *orig_tab5;
    uint32_t last_menu;
    void    *vtable[72];
    void    *root_children[8];
    Node     tab_node;
    Node     pages[MAX_PAGES];
    Node     items[MAX_MODS];
    Node     opts[2];
    void    *page_ptrs[MAX_PAGES];
    void    *item_ptrs[MAX_MODS];
    void    *opt_ptrs[2];
    Mod      mods[MAX_MODS];
    Mod      scan[MAX_MODS];   /* scratch for rescans */
    Mod      keep[MAX_MODS];   /* merge output (kept off the UI thread stack) */
    Mod      tmp;
    uint32_t scan_count;
    void **original_children;
    int32_t original_child_count;
} Ctx;

#define CTX_BYTES ((sizeof(Ctx) + 0xfffu) & ~0xfffu)

/* ---- v1 state layout (read-only, for takeover). Mirrors tools/native_camera/mod_loader.c. ---- */
typedef struct {
    char     name[32];
    char     path[64];
    uint8_t  enabled, hook_count, invalid, pad;
    uint32_t code_addr, code_size, state_addr, state_size, fnv1a_32;
    SmodV1Hook hooks[SMOD_MAX_HOOKS];
} V1Mod;
typedef struct {
    uint32_t magic, mod_count, page_count;
    uint8_t  scanned, vtable_copied, pad[2];
    void    *orig_root, *orig_tab5, **orig_children;
    uint32_t last_menu;
    void    *vtable[72];
    void    *root_children[8];
    Node     fake_root, mods_tab_node, page_nodes[2], item_nodes[12];
    void    *page_ptrs[2], *item_ptrs[12];
    V1Mod    mods[12];
} V1State;
_Static_assert(sizeof(V1Mod) == 440, "v1 ModEntry layout");

static Ctx *ctx_get(void) {
    ShimState *s = *(ShimState *volatile *)MLV2_SHIM_SLOT;
    if (!s || s->magic != MLV2_SHIM_MAGIC) return 0;
    Ctx *c = (Ctx *)s->ctx;
    return (c && c->magic == CTX_MAGIC) ? c : 0;
}

/* ---------------- fake menu node vtable entries ---------------- */
static int node_ret0(void) { return 0; }
static int node_ret1(void) { return 1; }
static int node_child_count(Node *n, int32_t *out) { *out = n->child_count; return 1; }
static int node_child(Node *n, int32_t idx1, void **out) {
    if (idx1 >= 1 && idx1 <= n->child_count) { *out = n->children[idx1 - 1]; return 1; }
    *out = 0;
    return 0;
}
static int node_cur_child(Node *n, void **out) {
    if (n->child_count > 0) { *out = n->children[0]; return 1; }
    *out = 0;
    return 0;
}
static int node_cur_idx(Node *n, int32_t *out) { (void)n; *out = 1; return 1; }

static int node_int_prop(Node *n, int32_t prop, int32_t *out) {
    if (prop == 11) { *out = 0; return 1; }
    Ctx *c = ctx_get();
    if (!c) return 0;
    if (prop == 12 && (uint32_t)n >= (uint32_t)&c->items[0] && (uint32_t)n < (uint32_t)&c->items[MAX_MODS]) {
        *out = 0;   /* option item type 0: OK maps to Action1 (E0523) */
        return 1;
    }
    if (n == &c->tab_node && c->orig_tab5) {
        typedef int (*fn_t)(void *, int32_t, int32_t *);
        return ((fn_t)c->orig_tab5->vtable[23])(c->orig_tab5, prop, out);
    }
    return 0;
}

static int node_resid_prop(Node *n, int32_t prop, uint32_t *out) {
    Ctx *c = ctx_get();
    if (c && n == &c->tab_node && (prop == 29 || prop == 30 || prop == 31)) {
        /* E0522 built-in tab artwork: 29 inactive, 30 current, 31 tab-bar focus. */
        *out = prop == 29 ? 0x020cc012u : 0x020cc000u + (uint32_t)prop - 5u;
        return 1;
    }
    if (c && n == &c->tab_node && prop == 2 && c->orig_tab5) {
        typedef int (*fn_t)(void *, int32_t, uint32_t *);
        return ((fn_t)c->orig_tab5->vtable[32])(c->orig_tab5, prop, out);
    }
    *out = 0;
    return 0;
}

static int node_is_disabled(Node *n, uint8_t *out) { (void)n; if (out) *out = 0; return 0; }

/* Point the core-owned vtable entries at this core's functions (also after an upgrade). */
static void vtable_bind(Ctx *c) {
    if (!c->vtable_ready) {
        for (uint32_t i = 0; i < 72u; i++) c->vtable[i] = (void *)node_ret0;
        c->vtable[4] = (void *)node_child_count;
        c->vtable[9] = (void *)node_child;
        c->vtable[10] = (void *)node_cur_child;
        c->vtable[12] = (void *)node_cur_idx;
    } else {
        for (uint32_t i = 65u; i < 72u; i++) c->vtable[i] = (void *)node_ret0;
        c->vtable[35] = (void *)node_ret1;
    }
    c->vtable[23] = (void *)node_int_prop;
    c->vtable[32] = (void *)node_resid_prop;
    c->vtable[40] = (void *)node_is_disabled;
}

/* ---------------- scanning ---------------- */
struct dirent64 { uint64_t ino; int64_t off; uint16_t reclen; uint8_t type; char name[1]; };

static int ends_with_mod(const char *s) {
    int n = 0;
    while (s[n]) n++;
    return n > 4 && s[n - 4] == '.' && s[n - 3] == 'm' && s[n - 2] == 'o' && s[n - 1] == 'd';
}

static void join(char *out, uint32_t cap, const char *dir, const char *file) {
    uint32_t i = 0;
    while (*dir && i + 2 < cap) out[i++] = *dir++;
    out[i++] = '/';
    while (*file && i + 1 < cap) out[i++] = *file++;
    out[i] = 0;
}

static const char *base_name(const char *p) {
    const char *b = p;
    for (; *p; p++) if (*p == '/') b = p + 1;
    return b;
}

static int already_listed(Ctx *c, const char *path) {
    for (uint32_t i = 0; i < c->scan_count; i++)
        if (!ml_strcmp(base_name(c->scan[i].path), base_name(path))) return 1;
    return 0;
}

/* Parse one .mod header into m; returns 1 if valid. */
static int parse_mod(const char *path, Mod *m) {
    int fd = ml_open(path, ML_O_RDONLY);
    if (fd < 0) return 0;
    uint8_t hdr[68];
    int ok = ml_read(fd, hdr, sizeof(hdr)) == (int)sizeof(hdr);
    const SmodV1Header *h1 = (const SmodV1Header *)hdr;
    const SmodV2Header *h2 = (const SmodV2Header *)hdr;
    ml_memset(m, 0, sizeof(*m));
    if (ok && h1->magic == SMOD_MAGIC && h1->version == 1u && h1->hook_count <= SMOD_MAX_HOOKS &&
        h1->code_size && !(h1->code_size & 3u) && h1->code_addr >= 0x00100000u) {
        m->kind = KIND_V1;
        m->flags = (uint8_t)h1->flags;
        m->hook_count = (uint8_t)h1->hook_count;
        m->code_size = h1->code_size;
        m->extra_size = h1->state_size;
        m->addr = h1->code_addr;
        m->state_addr = h1->state_addr;
        m->fnv = h1->fnv1a_32;
        ml_memcpy(m->name, h1->name, 31);
        for (uint32_t i = 0; i < m->hook_count && ok; i++) {
            SmodV1Hook hk;
            ok = ml_read(fd, &hk, sizeof(hk)) == (int)sizeof(hk) && hk.patch_size >= 1u &&
                 hk.patch_size <= SMOD_MAX_PATCH && hk.target_addr >= 0x00100000u;
            if (!ok) break;
            m->hooks[i].target = hk.target_addr;
            m->hooks[i].size = (uint8_t)hk.patch_size;
            m->hooks[i].fixup = SMOD_V2_NO_FIXUP;
            ml_memcpy(m->hooks[i].orig, hk.orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[i].patch, hk.hook_bytes, SMOD_MAX_PATCH);
        }
        m->data_off = 68u + m->hook_count * 40u;
    } else if (ok && h2->magic == SMOD_MAGIC && h2->version == 2u && h2->hook_count <= SMOD_MAX_HOOKS &&
               h2->code_size && !(h2->code_size & 3u) && h2->code_size + h2->bss_size <= 1024u * 1024u &&
               (!h2->on_off || ((h2->on_off & 1u) && h2->on_off < h2->code_size)) &&
               (!h2->off_off || ((h2->off_off & 1u) && h2->off_off < h2->code_size))) {
        m->kind = KIND_V2;
        m->flags = (uint8_t)h2->flags;
        m->hook_count = (uint8_t)h2->hook_count;
        m->code_size = h2->code_size;
        m->extra_size = h2->bss_size;
        m->on_off = h2->on_off;
        m->off_off = h2->off_off;
        m->fnv = h2->fnv1a_32;
        ml_memcpy(m->name, h2->name, 31);
        for (uint32_t i = 0; i < m->hook_count && ok; i++) {
            SmodV2Hook hk;
            ok = ml_read(fd, &hk, sizeof(hk)) == (int)sizeof(hk) && hk.patch_size >= 1u &&
                 hk.patch_size <= SMOD_MAX_PATCH && hk.target_addr >= 0x00100000u &&
                 (hk.fixup_off == SMOD_V2_NO_FIXUP || hk.fixup_off + 4u <= hk.patch_size);
            if (!ok) break;
            m->hooks[i].target = hk.target_addr;
            m->hooks[i].size = hk.patch_size;
            m->hooks[i].fixup = hk.fixup_off;
            ml_memcpy(m->hooks[i].orig, hk.orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[i].patch, hk.tmpl, SMOD_MAX_PATCH);
        }
        m->data_off = 68u + m->hook_count * 40u;
    } else {
        ok = 0;
    }
    ml_close(fd);
    if (!ok) { ml_log("scan_bad", h1->magic); return 0; }
    ml_memcpy(m->path, path, 63);
    m->name[31] = 0;
    m->path[63] = 0;
    m->present = 1;
    return 1;
}

static void scan_dir(Ctx *c, const char *dir) {
    int dfd = ml_open(dir, ML_O_RDONLY | ML_O_DIRECTORY);
    if (dfd < 0) return;
    uint8_t buf[512];
    int n;
    while ((n = ml_getdents64(dfd, buf, sizeof(buf))) > 0 && c->scan_count < MAX_MODS) {
        for (int pos = 0; pos < n && c->scan_count < MAX_MODS;) {
            struct dirent64 *d = (struct dirent64 *)(buf + pos);
            pos += d->reclen;
            if (!ends_with_mod(d->name)) continue;
            char path[64];
            join(path, sizeof(path), dir, d->name);
            if (already_listed(c, path)) continue;   /* first folder wins for a given file name */
            if (parse_mod(path, &c->scan[c->scan_count])) c->scan_count++;
        }
    }
    ml_close(dfd);
}

/* Rescan every folder and merge: enabled mods always stay; disabled ones follow the files. */
static void rescan(Ctx *c) {
    /* Folders in priority order, NUL-separated (built at run time; no pointer table). */
    const char *dirs = "/mnt/mmca1/mods\0/media/mmca1/mods\0/mnt/sd/mods\0/tmp_bt/mods\0";
    int managed = ml_open(MLV2_SD_MANAGED, ML_O_RDONLY);
    if (managed >= 0) {
        ml_close(managed);
        dirs = "/tmp_bt/mods\0";
    }
    c->scan_count = 0;
    for (const char *d = dirs; *d; ) {
        scan_dir(c, d);
        while (*d) d++;
        d++;
    }
    Mod *out = c->scan;
    Mod *keep = c->keep;
    uint32_t keep_n = 0;
    /* Pass 1: enabled mods stay, refreshed with presence / staleness. */
    for (uint32_t i = 0; i < c->mod_count; i++) {
        Mod *m = &c->mods[i];
        if (!m->enabled) continue;
        m->present = 0;
        m->stale = 0;
        for (uint32_t j = 0; j < c->scan_count; j++)
            if (!ml_strcmp(out[j].path, m->path)) { m->present = 1; m->stale = out[j].fnv != m->fnv; out[j].present = 2; }
        ml_memcpy(&keep[keep_n++], m, sizeof(Mod));
    }
    /* Pass 2: disabled entries are replaced by the current files (keeping any v2 mapping). */
    for (uint32_t j = 0; j < c->scan_count && keep_n < MAX_MODS; j++) {
        if (out[j].present == 2) continue;
        Mod *nm = &out[j];
        for (uint32_t i = 0; i < c->mod_count; i++) {
            Mod *old = &c->mods[i];
            if (old->enabled || ml_strcmp(old->path, nm->path)) continue;
            if (old->fnv == nm->fnv) nm->invalid = old->invalid;   /* unchanged file keeps its ERR state */
            if (old->kind == KIND_V2 && nm->kind == KIND_V2 && old->addr &&
                old->map_size >= nm->code_size + nm->extra_size) {
                nm->addr = old->addr;
                nm->map_size = old->map_size;
            }
        }
        ml_memcpy(&keep[keep_n++], nm, sizeof(Mod));
    }
    /* Sort by display name for a stable menu order. */
    for (uint32_t i = 1; i < keep_n; i++)
        for (uint32_t k = i; k > 0 && ml_strcmp(keep[k - 1].name, keep[k].name) > 0; k--) {
            ml_memcpy(&c->tmp, &keep[k], sizeof(Mod));
            ml_memcpy(&keep[k], &keep[k - 1], sizeof(Mod));
            ml_memcpy(&keep[k - 1], &c->tmp, sizeof(Mod));
        }
    for (uint32_t i = 0; i < keep_n; i++) ml_memcpy(&c->mods[i], &keep[i], sizeof(Mod));
    c->mod_count = keep_n;
}

/* ---------------- menu tree ---------------- */
static void build_tree(Ctx *c) {
    uint32_t n_items = c->mod_count ? c->mod_count : 1u;
    uint32_t n_pages = n_items > 6u ? 2u : 1u;
    c->page_count = n_pages;
    for (uint32_t i = 0; i < n_items && i < MAX_MODS; i++) {
        uint32_t pos1 = i - (i >= 6u ? 6u : 0u) + 1u;
        Node *it = &c->items[i];
        it->vtable = c->vtable;
        it->children = c->opt_ptrs;
        it->child_count = 2;
        it->parent = &c->pages[i >= 6u ? 1u : 0u];
        it->layer_depth = 4;
        it->selected_idx = 1;
        it->s1c = it->s20 = pos1;
        it->s24 = 1;
        c->item_ptrs[i] = it;
    }
    for (uint32_t o = 0; o < 2u; o++) {
        Node *op = &c->opts[o];
        op->vtable = c->vtable;
        op->children = 0;
        op->child_count = 0;
        op->parent = &c->items[0];
        op->selected_idx = -1;
        op->s1c = op->s20 = o + 1u;
        op->s24 = o ? 1u : 2u;
        c->opt_ptrs[o] = op;
    }
    for (uint32_t p = 0; p < n_pages; p++) {
        uint32_t start = p * 6u, rem = n_items - start;
        Node *pg = &c->pages[p];
        pg->vtable = c->vtable;
        pg->children = &c->item_ptrs[start];
        pg->child_count = (int32_t)(rem > 6u ? 6u : rem);
        pg->parent = &c->tab_node;
        pg->layer_depth = 3;
        pg->selected_idx = 1;
        pg->s1c = pg->s20 = p + 1u;
        pg->s24 = 1;
        c->page_ptrs[p] = pg;
    }
    c->tab_node.vtable = c->vtable;
    c->tab_node.children = c->page_ptrs;
    c->tab_node.child_count = (int32_t)n_pages;
    c->tab_node.layer_depth = 2;
    c->tab_node.selected_idx = 1;
    c->tab_node.s1c = c->tab_node.s20 = 7;
    c->tab_node.s24 = 1;
}

static void toggle(Mod *m);

static int sd_disabled(Ctx *c) {
    int fd = ml_open(MLV2_SD_DISABLED, ML_O_RDONLY);
    if (fd < 0) return 0;
    ml_close(fd);
    for (uint32_t i = 0; i < c->mod_count; i++) {
        Mod *m = &c->mods[i];
        if (m->enabled) toggle(m);
    }
    return 1;
}

static void sd_acknowledge_stop(const Ctx *c) {
    for (uint32_t i = 0; i < c->mod_count; i++)
        if (c->mods[i].enabled) return;
    int fd = ml_open(MLV2_SD_STOPPED, ML_O_WR_CREAT_APP);
    if (fd >= 0) ml_close(fd);
}

static uint32_t core_before_set_root(uint32_t menu, uint32_t root_addr) {
    (void)menu;
    Node *root = (Node *)root_addr;
    if (!root || root_addr < 0x00100000u || (root->child_count != 6 && root->child_count != 7)) return root_addr;
    if (!root->vtable || (uint32_t)root->vtable < 0x00100000u || !root->children ||
        (uint32_t)root->children < 0x00100000u) return root_addr;
    Ctx *c = ctx_get();
    if (!c) return root_addr;
    if (sd_disabled(c)) {
        if (root == c->orig_root && root->children == c->root_children && c->original_children) {
            root->children = c->original_children;
            root->child_count = c->original_child_count;
        }
        if (root->children != c->root_children) sd_acknowledge_stop(c);
        return root_addr;
    }
    if (!c->vtable_ready) {
        for (uint32_t i = 0; i < 65u; i++) c->vtable[i] = root->vtable[i];
        c->vtable_ready = 1;
        vtable_bind(c);
    }
    rescan(c);
    build_tree(c);
    if (root->children != c->root_children) {
        c->orig_root = root;
        c->original_children = root->children;
        c->original_child_count = root->child_count;
        for (uint32_t i = 0; i < 6u; i++) c->root_children[i] = root->children[i];
        c->orig_tab5 = (Node *)root->children[5];
        root->children = c->root_children;
    }
    c->tab_node.parent = root;
    c->root_children[6] = &c->tab_node;
    root->child_count = 7;
    return root_addr;
}

static const char *state_text(const Mod *m) {
    if (m->invalid || (m->enabled && !m->present)) return "ERR";
    if (m->enabled) return m->stale ? "ON*" : "ON";
    return "OFF";
}

static void core_after_refresh(uint32_t menu) {
    Ctx *c = ctx_get();
    if (!c) return;
    if (sd_disabled(c)) return;
    c->last_menu = menu;
    if (c->orig_root && c->orig_root->children == c->root_children) {
        c->root_children[6] = &c->tab_node;
        c->orig_root->child_count = 7;
    }
    if (*(volatile uint32_t *)(menu + 0x1f0u) != 7u) return;
    typedef void (*set_text_t)(void *, const char *);
    set_text_t set_text = (set_text_t)MLV2_SET_TEXT;
    set_text((void *)(menu + 0x5470u), "Mods");
    uint32_t page = *(volatile uint32_t *)(menu + 0x1f4u);
    if (!page) page = 1;
    uint32_t base = (page - 1u) * 6u;
    for (uint32_t r = 0; r < 6u; r++) {
        uint32_t btn = menu + 0x9350u + r * 0x1118u, idx = base + r;
        if (idx < c->mod_count) {
            set_text((void *)(btn + 0x7c8u), c->mods[idx].name);
            set_text((void *)(btn + 0xb60u), state_text(&c->mods[idx]));
        } else if (!r && !c->mod_count) {
            set_text((void *)(btn + 0x7c8u), "No mods found");
            set_text((void *)(btn + 0xb60u), "");
        }
    }
}

/* ---------------- loading and toggling ---------------- */
static void hook_bytes(const Mod *m, const Hook *hk, uint8_t *bytes) {
    ml_memcpy(bytes, hk->patch, SMOD_MAX_PATCH);
    if (m->kind == KIND_V2 && hk->fixup != SMOD_V2_NO_FIXUP) {
        uint32_t entry;
        ml_memcpy(&entry, bytes + hk->fixup, 4);
        entry += m->addr;
        ml_memcpy(bytes + hk->fixup, &entry, 4);
    }
}

static int hooks_revert(Mod *m, uint32_t upto) {
    int failed = 0;
    for (uint32_t h = 0; h < upto; h++) {
        Hook *hk = &m->hooks[h];
        void *target = (void *)hk->target;
        if (!ml_memcmp(target, hk->orig, hk->size)) continue;
        uint8_t bytes[SMOD_MAX_PATCH];
        hook_bytes(m, hk, bytes);
        if (ml_memcmp(target, bytes, hk->size) || ml_make_rwx(hk->target, hk->size) < 0) {
            failed = 1;
            continue;
        }
        ml_memcpy(target, hk->orig, hk->size);
        ml_flush_all(hk->target, hk->target + hk->size);
        if (ml_memcmp(target, hk->orig, hk->size)) failed = 1;
    }
    return failed;
}

static void call_entry(uint32_t addr, int arg) { ((void (*)(int))(addr | 1u))(arg); }

static int load_code(Mod *m) {
    uint32_t dst = m->addr, need = m->code_size + m->extra_size;
    if (m->kind == KIND_V2 && (!dst || m->map_size < need)) {
        dst = ml_mmap_rwx(need);
        if (!dst) return 4;
        m->addr = dst;
        m->map_size = (need + 0xfffu) & ~0xfffu;   /* old mappings are never unmapped */
    } else if (m->kind == KIND_V1 && ml_make_rwx(dst, m->code_size) < 0) {
        return 4;
    }
    int fd = ml_open(m->path, ML_O_RDONLY);
    if (fd < 0) return 3;
    ml_lseek(fd, m->data_off);
    int n = ml_read(fd, (void *)dst, m->code_size);
    if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_STATE_INIT)) {
        if (ml_read(fd, (void *)m->state_addr, m->extra_size) != (int)m->extra_size) n = -1;
    } else if (m->kind == KIND_V1 && m->state_addr && m->extra_size) {
        ml_memset((void *)m->state_addr, 0, m->extra_size);
    } else if (m->kind == KIND_V2 && m->extra_size) {
        ml_memset((void *)(dst + m->code_size), 0, m->extra_size);
    }
    ml_close(fd);
    if (n != (int)m->code_size) return 5;
    if (ml_fnv1a((const uint8_t *)dst, m->code_size) != m->fnv) return 6;
    ml_flush_all(dst, dst + m->code_size);
    return 0;
}

static void toggle(Mod *m) {
    if (!m->enabled) {
        if (m->invalid) return;
        if (m->stale) { m->invalid = 1; return; }
        for (uint32_t h = 0; h < m->hook_count; h++)
            if (ml_memcmp((void *)m->hooks[h].target, m->hooks[h].orig, m->hooks[h].size)) {
                ml_log("tgl_err", 2u);   /* target bytes differ: another patch is present */
                m->invalid = 1;
                return;
            }
        int rc = load_code(m);
        if (rc) { ml_log("tgl_err", (uint32_t)rc); if (rc != 3) m->invalid = 1; return; }
        for (uint32_t h = 0; h < m->hook_count; h++) {
            Hook *hk = &m->hooks[h];
            uint8_t bytes[SMOD_MAX_PATCH];
            hook_bytes(m, hk, bytes);
            if (ml_make_rwx(hk->target, hk->size) < 0) {
                m->enabled = hooks_revert(m, h);
                m->off_called = 1;  /* ON lifecycle never ran. */
                ml_log("tgl_err", 7u);
                m->invalid = 1;
                return;
            }
            ml_memcpy((void *)hk->target, bytes, hk->size);
            ml_flush_all(hk->target, hk->target + hk->size);
        }
        m->enabled = 1;
        m->off_called = 0;
        if (m->kind == KIND_V2 && m->on_off) call_entry(m->addr + m->on_off, 1);
        if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_LIFECYCLE)) call_entry(m->addr, 1);
        ml_log("mod_on", m->addr);
    } else {
        if (!m->off_called) {
            if (m->kind == KIND_V2 && m->off_off) call_entry(m->addr + m->off_off, 0);
            if (m->kind == KIND_V1 && (m->flags & SMOD_V1_FLAG_LIFECYCLE)) call_entry(m->addr, 0);
            m->off_called = 1;
        }
        if (hooks_revert(m, m->hook_count)) {
            m->invalid = 1;
            ml_log("mod_off_failed", m->addr);
            return;
        }
        m->invalid = 0;
        m->enabled = 0;
        ml_log("mod_off", m->addr);
    }
}

static int core_before_enter(uint32_t menu, uint32_t leaf) {
    Ctx *c = ctx_get();
    if (!c) return -1;
    int disabled = sd_disabled(c);
    uint32_t lo = (uint32_t)&c->items[0], hi = (uint32_t)&c->items[MAX_MODS];
    if (leaf < lo || leaf >= hi) return -1;
    if (disabled) return 0;
    uint32_t idx = 0;
    while (idx < MAX_MODS && (uint32_t)&c->items[idx] != leaf) idx++;
    if (idx >= c->mod_count) return 0;
    Mod *m = &c->mods[idx];
    toggle(m);
    typedef void (*set_text_t)(void *, const char *);
    uint32_t row = idx >= 6u ? idx - 6u : idx;
    ((set_text_t)MLV2_SET_TEXT)((void *)(menu + 0x9350u + row * 0x1118u + 0xb60u), state_text(m));
    return 0;
}

/* ---------------- init / takeover / upgrade ---------------- */
static void import_v1(Ctx *c) {
    volatile uint32_t *anchor = (volatile uint32_t *)MLV2_V1_ANCHOR;
    if (anchor[0] != MLV2_V1_MAGIC || !anchor[1]) return;
    V1State *v = (V1State *)anchor[1];
    for (uint32_t i = 0; i < v->mod_count && i < 12u && c->mod_count < MAX_MODS; i++) {
        V1Mod *o = &v->mods[i];
        if (!o->enabled) continue;
        Mod *m = &c->mods[c->mod_count++];
        ml_memset(m, 0, sizeof(*m));
        ml_memcpy(m->name, o->name, 31);
        ml_memcpy(m->path, o->path, 63);
        m->kind = KIND_V1;
        m->enabled = 1;
        m->flags = o->pad;          /* v1 stored the SMOD flags in pad */
        m->hook_count = o->hook_count;
        m->code_size = o->code_size;
        m->addr = o->code_addr;
        m->state_addr = o->state_addr;
        m->extra_size = o->state_size;
        m->fnv = o->fnv1a_32;
        m->present = 1;
        m->data_off = 68u + o->hook_count * 40u;
        for (uint32_t h = 0; h < o->hook_count && h < SMOD_MAX_HOOKS; h++) {
            m->hooks[h].target = o->hooks[h].target_addr;
            m->hooks[h].size = (uint8_t)o->hooks[h].patch_size;
            m->hooks[h].fixup = SMOD_V2_NO_FIXUP;
            ml_memcpy(m->hooks[h].orig, o->hooks[h].orig_bytes, SMOD_MAX_PATCH);
            ml_memcpy(m->hooks[h].patch, o->hooks[h].hook_bytes, SMOD_MAX_PATCH);
        }
        ml_log("v1_import", m->addr);
    }
}

__attribute__((section(".text.core_init"), used))
int core_init(ShimState *s) {
    Ctx *c = (Ctx *)s->ctx;
    if (c && (c->magic != CTX_MAGIC || c->version != CTX_VERSION)) {
        for (uint32_t i = 0; i < MAX_MODS; i++)
            if (c->magic == CTX_MAGIC && i < c->mod_count && c->mods[i].enabled) { ml_log("core_refuse", c->version); return -1; }
        c = 0;
    }
    if (!c) {
        c = (Ctx *)ml_mmap_rwx(CTX_BYTES);
        if (!c) return -2;
        ml_memset(c, 0, sizeof(*c));
        c->magic = CTX_MAGIC;
        c->version = CTX_VERSION;
        import_v1(c);
        /* Fresh install (no v1): provide the anchor block the tab artwork uses for its cache. */
        volatile uint32_t *anchor = (volatile uint32_t *)MLV2_V1_ANCHOR;
        if (anchor[0] != MLV2_V1_MAGIC || !anchor[1]) {
            uint32_t blk = ml_mmap_rwx(MLV2_V1_STATE_BYTES);
            if (blk && ml_make_rwx(MLV2_V1_ANCHOR, 8u) == 0) { anchor[1] = blk; anchor[0] = MLV2_V1_MAGIC; }
        }
    }
    vtable_bind(c);
    s->ctx = c;
    s->before_set_root = core_before_set_root;
    s->after_refresh = core_after_refresh;
    s->before_enter = core_before_enter;
    ml_log("core_init", CTX_VERSION);
    return 0;
}
