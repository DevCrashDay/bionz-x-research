/* SPDX-License-Identifier: GPL-2.0-or-later */
/*
 * CXD90045 CamCore register block at 0xF3010000 (64 KiB).
 *
 * The av-cam core maps this block 1:1 (virtual 0xF30x0000 = physical, see
 * docs/evidence/0564-qemu-ydet-window-configuration-reaches-camcore.md).
 * In the baseline access log (loop/runs/L0016/run1/qemu-d.log) it has 1012
 * distinct (kind, offset) pairs but only 28 distinct read offsets; the other
 * 984 offsets are write-only configuration.  This device stores every write
 * and gives each observed read offset a named rule instead of the catch-all
 * zero/LOG_UNIMP behaviour:
 *
 *  - RF   read back the last value written (the 16 read-modify-write words,
 *         plus 0x5020, which the guest writes 0x01030001 and then reads).
 *  - Z0   return 0: polled status words the guest never writes.  Each getter
 *         PC and mask comes from a gdbstub read watchpoint on the baseline
 *         build (loop/scratch/L0025/watch3.log, watch4.log, disasm2.txt);
 *         returning 0 is the baseline behaviour and no value is invented.
 *         The RMW load PCs below are from this lead's watchpoints on the v11
 *         baseline (loop/scratch/L0027/watch*.log, disasm.txt); each one is
 *         an ldr/bfi/str sequence to the same word.
 *
 * All observed reads are in the table, so no read offset depends on a
 * blanket fallback.  An unlisted read returns 0 and is logged with
 * LOG_UNIMP, so a new read offset is never silent.
 *
 * The off-by-default "trace" property logs every access (kind, offset,
 * size, value) with qemu_log, so the guest write stream can be compared
 * against the baseline unmodelled-access log without adding oracle lines.
 */

#include "qemu/osdep.h"
#include "hw/core/sysbus.h"
#include "hw/core/qdev-properties.h"
#include "qemu/log.h"
#include "qemu/cutils.h"
#include "qemu/module.h"
#include "qom/object.h"

#define TYPE_CXD90045_CAMCORE "cxd90045-camcore"
OBJECT_DECLARE_SIMPLE_TYPE(CamCoreState, CXD90045_CAMCORE)

enum CamCoreRuleKind {
    CAM_RF,     /* read back the last write */
    CAM_ZERO,   /* always read 0 (status the guest never writes) */
};

typedef struct CamCoreRule {
    uint32_t off;
    enum CamCoreRuleKind kind;
    uint32_t mask;      /* guest getter mask, informational for Z0 */
    const char *why;    /* reading PC / reason */
} CamCoreRule;

/*
 * One entry per observed read offset (28).  PCs are the Thumb load
 * instructions; the gdbstub watchpoint reports the following instruction.
 */
static const CamCoreRule camcore_rules[] = {
    { 0x4b00, CAM_RF,   0xffffffff, "RMW at 0x63cdf21a: ldr/bfi/str same word" },
    { 0x4b04, CAM_RF,   0xffffffff, "RMW at 0x63cdf25e: ldr/bfi/str same word" },
    { 0x5020, CAM_RF,   0xffffffff, "read back guest write 0x01030001; "
                                    "nibble getters 0x63cd0a1c/0x63cd0a28" },
    { 0x5600, CAM_ZERO, 0x00001fff, "status, never written; getter 0x63cd6c72" },
    { 0x5604, CAM_ZERO, 0x00000fff, "status, never written; getter 0x63cd6c82" },
    { 0x5a04, CAM_RF,   0xffffffff, "RMW at 0x63cd7cfc: ldr/bfi/str same word" },
    { 0x5a14, CAM_RF,   0xffffffff, "RMW at 0x63cd7d56: ldr/bfi/str same word" },
    { 0x5a18, CAM_RF,   0xffffffff, "RMW at 0x63cd7d7a: ldr/bfi/str same word" },
    { 0x5a1c, CAM_RF,   0xffffffff, "RMW at 0x63cd7d9e: ldr/bfi/str same word" },
    { 0x5a28, CAM_RF,   0xffffffff, "RMW at 0x63cd7dda: ldr/bfi/str same word" },
    { 0x5a2c, CAM_RF,   0xffffffff, "RMW at 0x63cd7dfe: ldr/bfi/str same word" },
    { 0x5a30, CAM_RF,   0xffffffff, "RMW at 0x63cd7e22: ldr/bfi/str same word" },
    { 0x5a48, CAM_RF,   0xffffffff, "RMW at 0x63cd7e76: ldr/bfi/str same word" },
    { 0x5a4c, CAM_RF,   0xffffffff, "RMW at 0x63cd7e9a: ldr/bfi/str same word" },
    { 0x5ac8, CAM_RF,   0xffffffff, "RMW at 0x63cd800e: ldr/bfi/str same word" },
    { 0x5acc, CAM_RF,   0xffffffff, "RMW at 0x63cd8032: ldr/bfi/str same word" },
    { 0x5ad0, CAM_RF,   0xffffffff, "RMW at 0x63cd8058: ldr/bfi/str same word" },
    { 0x5ad4, CAM_RF,   0xffffffff, "RMW at 0x63cd807e: ldr/bfi/str same word" },
    { 0x5b80, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8102" },
    { 0x5b84, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8112" },
    { 0x5b8c, CAM_ZERO, 0x000007ff, "status, never written; getter 0x63cd8132" },
    { 0x5b90, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd8142" },
    { 0x5b94, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd8152" },
    { 0x5b98, CAM_ZERO, 0x0fffffff, "status, never written; getter 0x63cd815e" },
    { 0x5b9c, CAM_ZERO, 0x00003fff, "status, never written; getter 0x63cd816e" },
    { 0x5ba0, CAM_ZERO, 0xffffffff, "status, never written; getter 0x63cd817e" },
    { 0x5ba4, CAM_ZERO, 0x0fffffff, "status, never written; getter 0x63cd818a" },
    { 0x5e00, CAM_RF,   0xffffffff, "RMW at 0x63cd9396: ldr/bfi/str same word" },
};

struct CamCoreState {
    SysBusDevice parent_obj;
    MemoryRegion mr;
    GHashTable *regs;
    uint64_t size;
    uint64_t base;      /* for the mapping comment only */
    char *name;
    bool trace;
};

static const CamCoreRule *camcore_rule(uint32_t off)
{
    size_t i;

    for (i = 0; i < ARRAY_SIZE(camcore_rules); i++) {
        if (camcore_rules[i].off == off) {
            return &camcore_rules[i];
        }
    }
    return NULL;
}

static uint64_t camcore_read(void *opaque, hwaddr off, unsigned size)
{
    CamCoreState *s = opaque;
    hwaddr w = off & ~3ULL;
    const CamCoreRule *r = camcore_rule(w);
    uint32_t v = 0;

    if (!r) {
        qemu_log_mask(LOG_UNIMP, "%s: unlisted read offset 0x%05x size %u\n",
                      s->name ? s->name : "cxd90045-camcore", (unsigned)off,
                      size);
    } else if (r->kind == CAM_RF) {
        v = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs,
                              GUINT_TO_POINTER((guint)w)));
    }
    /* CAM_ZERO and unlisted offsets read 0. */
    v >>= (off & 3) * 8;
    if (size < 4) {
        v &= (1u << (size * 8)) - 1;
    }
    if (s->trace) {
        qemu_log("camcore-trace: %s read off 0x%05x size %u value 0x%08x\n",
                 s->name ? s->name : "cxd90045-camcore", (unsigned)off, size,
                 v);
    }
    return v;
}

static void camcore_write(void *opaque, hwaddr off, uint64_t v, unsigned size)
{
    CamCoreState *s = opaque;
    hwaddr w = off & ~3ULL;
    uint32_t old = GPOINTER_TO_UINT(g_hash_table_lookup(s->regs,
                                 GUINT_TO_POINTER((guint)w)));
    unsigned sh = (off & 3) * 8;
    uint32_t mask = size >= 4 ? 0xffffffffu : ((1u << (size * 8)) - 1) << sh;
    uint32_t nv = (old & ~mask) | (((uint32_t)v << sh) & mask);

    if (s->trace) {
        qemu_log("camcore-trace: %s write off 0x%05x size %u value 0x%08x\n",
                 s->name ? s->name : "cxd90045-camcore", (unsigned)off, size,
                 (uint32_t)v);
    }
    g_hash_table_insert(s->regs, GUINT_TO_POINTER((guint)w),
                        GUINT_TO_POINTER(nv));
}

static const MemoryRegionOps camcore_ops = {
    .read = camcore_read,
    .write = camcore_write,
    .endianness = DEVICE_LITTLE_ENDIAN,
    .valid = { .min_access_size = 1, .max_access_size = 4 },
    .impl = { .min_access_size = 1, .max_access_size = 4 },
};

static void camcore_reset(DeviceState *dev)
{
    CamCoreState *s = CXD90045_CAMCORE(dev);
    g_hash_table_remove_all(s->regs);
}

static void camcore_realize(DeviceState *dev, Error **errp)
{
    CamCoreState *s = CXD90045_CAMCORE(dev);
    s->regs = g_hash_table_new(g_direct_hash, g_direct_equal);
    memory_region_init_io(&s->mr, OBJECT(dev), &camcore_ops, s,
                          s->name ? s->name : "cxd90045-camcore", s->size);
    sysbus_init_mmio(SYS_BUS_DEVICE(dev), &s->mr);
}

static const Property camcore_props[] = {
    DEFINE_PROP_UINT64("size", CamCoreState, size, 0x10000),
    DEFINE_PROP_UINT64("base", CamCoreState, base, 0),
    DEFINE_PROP_STRING("name", CamCoreState, name),
    DEFINE_PROP_BOOL("trace", CamCoreState, trace, false),
};

static void camcore_class_init(ObjectClass *klass, const void *data)
{
    DeviceClass *dc = DEVICE_CLASS(klass);
    dc->realize = camcore_realize;
    device_class_set_legacy_reset(dc, camcore_reset);
    device_class_set_props(dc, camcore_props);
}

static const TypeInfo camcore_info = {
    .name = TYPE_CXD90045_CAMCORE,
    .parent = TYPE_SYS_BUS_DEVICE,
    .instance_size = sizeof(CamCoreState),
    .class_init = camcore_class_init,
};

static void camcore_register(void)
{
    type_register_static(&camcore_info);
}
type_init(camcore_register)
