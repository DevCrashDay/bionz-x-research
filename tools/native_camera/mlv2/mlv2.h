/* Mod Loader v2: shared ABI between the shim (fixed cave), the core (position-independent,
 * loaded into mmap memory) and SMOD v2 mods (position-independent, loaded by the core).
 * See docs/MOD_LOADER_V2_DESIGN.md.
 */
#ifndef MLV2_H
#define MLV2_H
#include <stdint.h>

/* ---- Firmware addresses (ILCE-6400, identity-checked by the guard) ---- */
#define MLV2_VIEW_BASE        0x155e6000u
#define MLV2_SET_TEXT         (MLV2_VIEW_BASE + 0x14f6d0u + 1u)
#define MLV2_SET_MODE         (MLV2_VIEW_BASE + 0x155a68u + 1u)
#define MLV2_V1_ANCHOR        0x1615ec40u   /* v1 anchor {magic, state}; also used by the tab icon */
#define MLV2_V1_MAGIC         0x444f4d53u   /* "SMOD" */
#define MLV2_V1_STATE_BYTES   8192u         /* tab icon cache lives at state + 8180 */

/* ---- Shim ---- */
#define MLV2_SHIM_ADDR        0x05662760u   /* /usr/lib/libtestcmd.so RX zero tail, 2208 bytes */
#define MLV2_SHIM_CAP         2208u
#define MLV2_SHIM_SLOT        MLV2_SHIM_ADDR  /* first word of the shim: ShimState pointer */
#define MLV2_CORE_PATH        "/tmp_bt/mlv2/core.bin"
#define MLV2_SD_MANAGED       "/tmp_bt/mlv2/sd-managed"
#define MLV2_SD_DISABLED      "/tmp_bt/mlv2/sd-disabled"
#define MLV2_SD_STOPPED       "/tmp_bt/mlv2/sd-stopped"

/* ---- Core image file ---- */
#define MLV2_CORE_MAGIC       0x52434c4du   /* "MLCR" */
#define MLV2_CORE_VERSION     2u
#define MLV2_CORE_MAX         (64u * 1024u)
typedef struct {
    uint32_t magic;
    uint32_t version;
    uint32_t code_size;      /* bytes following this header */
    uint32_t fnv1a_32;       /* of the code bytes */
    uint32_t init_off;       /* Thumb entry offset (bit 0 set) of core_init(ShimState *) */
} MlCoreHeader;

/* ---- State owned by the shim (one mmap page, never freed) ---- */
#define MLV2_SHIM_MAGIC       0x4853324du   /* "M2SH" */
typedef struct ShimState {
    uint32_t magic;
    uint32_t core_fnv;                     /* fnv of the core currently in use (0 = none) */
    uint32_t core_base;                    /* mmap base of the current core (old cores stay mapped) */
    uint32_t core_loads;                   /* number of successful core loads */
    void    *ctx;                          /* core context (versioned, survives core upgrades) */
    uint32_t (*before_set_root)(uint32_t menu, uint32_t root);
    void     (*after_refresh)(uint32_t menu);
    int      (*before_enter)(uint32_t menu, uint32_t leaf);
} ShimState;

/* ---- SMOD file formats ---- */
#define SMOD_MAGIC            0x444f4d53u   /* "SMOD" */
#define SMOD_MAX_HOOKS        8u
#define SMOD_MAX_PATCH        16u
#define SMOD_V1_FLAG_STATE_INIT 1u
#define SMOD_V1_FLAG_LIFECYCLE  2u

/* v1 header (68 bytes) and hook record (40 bytes): fixed-address mods. */
typedef struct __attribute__((packed)) {
    uint32_t magic, version, flags;
    char     name[32];
    uint32_t code_addr, code_size, state_addr, state_size, hook_count, fnv1a_32;
} SmodV1Header;
typedef struct __attribute__((packed)) {
    uint32_t target_addr, patch_size;
    uint8_t  orig_bytes[SMOD_MAX_PATCH];
    uint8_t  hook_bytes[SMOD_MAX_PATCH];
} SmodV1Hook;

/* v2 header (68 bytes) and hook record (40 bytes): position-independent mods.
 * Code follows the hooks; bss_size zero bytes follow the code in the same RWX mapping.
 * on_off / off_off are Thumb entry offsets (bit 0 set) or 0 for "none".
 * Hook: the core copies tmpl into the target. If fixup_off < patch_size - 3, the 32-bit word
 * at tmpl[fixup_off] is an offset into the mod and is replaced by (base + offset). */
#define SMOD_V2_NO_FIXUP      0xffu
typedef struct __attribute__((packed)) {
    uint32_t magic, version, flags;
    char     name[32];
    uint32_t code_size, fnv1a_32, bss_size, on_off, off_off, hook_count;
} SmodV2Header;
typedef struct __attribute__((packed)) {
    uint32_t target_addr;
    uint8_t  patch_size, fixup_off, pad[2];
    uint8_t  orig_bytes[SMOD_MAX_PATCH];
    uint8_t  tmpl[SMOD_MAX_PATCH];
} SmodV2Hook;

_Static_assert(sizeof(SmodV1Header) == 68 && sizeof(SmodV2Header) == 68, "SMOD header size");
_Static_assert(sizeof(SmodV1Hook) == 40 && sizeof(SmodV2Hook) == 40, "SMOD hook size");

#endif
