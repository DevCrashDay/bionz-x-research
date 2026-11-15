/*
 * Sony ILCE-6400 (BIONZ X / CXD90045 / ARM-CXD900X0) Machine Model
 *
 * Initial Milestone 1 bring-up machine derived from verified CXD90045
 * L2 hardware contracts (src/a6400/machine/soc_cxd90045.py):
 *   - Machine ID: 0x00000DA9 (3497, ARM-CXD900X0)
 *   - RAM bank 0: 0x00000000 .. 0x40000000 (1 GiB)
 *   - RAM bank 1: 0x80000000 .. 0xBF800000 (1016 MiB)
 *   - SRAM:       0xFE000000 .. 0xFE100000 (1 MiB)
 *   - A9MPCore:   0xF8000000 (GIC CPU @ 0xF8000100, GIC Dist @ 0xF8001000)
 *   - PL011 UART0:0xF2000000 (IRQ 150 -> SPI 118)
 *   - CXD4115 timer: 0xF2403000, channel n -> IRQ 153 + n (E0522)
 *   - 4 x Cortex-A5 (E0522: secondaries released through SRAM mailbox
 *     0xFE005010 + 4 * (cpu - 1) by cxd900x0_smp_init)
 *   - CRCDC Clk:  0xF2A31000 (+0x30 bit 0 ready)
 *
 * SPDX-License-Identifier: GPL-2.0-or-later
 */

#include "qemu/osdep.h"
#include "qapi/error.h"
#include "hw/arm/boot.h"
#include "hw/loader.h"
#include "qemu/error-report.h"
#include "system/reset.h"
#include "hw/boards.h"
#include "hw/irq.h"
#include "hw/char/pl011.h"
#include "hw/cpu/a9mpcore.h"
#include "hw/misc/unimp.h"
#include "hw/qdev-properties.h"
#include "hw/sd/sd.h"
#include "hw/sd/sdhci.h"
#include "hw/sysbus.h"
#include "system/blockdev.h"
#include "qemu/log.h"
#include "qemu/units.h"
#include "system/address-spaces.h"
#include "system/system.h"
#include "target/arm/cpu.h"

#define CXD90045_MACHINE_ID      0x00000DA9
#define CXD90045_RAM0_BASE       0x00000000ULL
#define CXD90045_RAM0_SIZE       (1 * GiB)
#define CXD90045_RAM1_BASE       0x80000000ULL
#define CXD90045_RAM1_SIZE       (1016 * MiB)
#define CXD90045_SRAM_BASE       0xFE000000ULL
#define CXD90045_SRAM_SIZE       (1 * MiB)

#define CXD90045_PERIPH_BASE     0xF8000000ULL
#define CXD90045_UART0_BASE      0xF2000000ULL
#define CXD90045_UART0_SPI       (150 - 32)
#define CXD90045_TIMER_BASE      0xF2403000ULL
#define CXD90045_TIMER_IRQ0      153
#define CXD90045_MAX_CPUS        4
#define CXD90045_CRCDC_BASE      0xF2A31000ULL
#define CXD90045_GPES_BAR_BASE   0xF1000000ULL
#define CXD90045_GPES_SPI        (76 - 32)
#define CXD90045_SUGI_CLK_BASE   0xF3400000ULL
#define CXD90045_DFS_CTRL_BASE   0xF2916000ULL
#define CXD90045_L2C_STUB_BASE   0xF8002000ULL
#define CXD90045_EMMC0_BASE      0xF0304000ULL
#define CXD90045_EMMC0_SPI       (227 - 32)
#define CXD90045_XDMAC0_BASE     0xF0000000ULL
#define CXD90045_XDMAC0_SPI_BASE (189 - 32)
#define CXD90045_XDMAC1_BASE     0xF0010000ULL
#define CXD90045_XDMAC1_SPI_BASE (193 - 32)
#define CXD90045_LDEC_BASE       0xF2903000ULL
#define CXD90045_LDEC_IN_BASE    0xF0020000ULL
#define CXD90045_LDEC_IN_SIZE    0x10000ULL
#define CXD90045_LDEC_SPI        (171 - 32)
#define CXD90045_F2A01_BASE      0xF2A01000ULL

#define CXD90045_NUM_IRQS        256

static uint64_t cxd90045_crcdc_read(void *opaque, hwaddr offset, unsigned size)
{
    /* +0x30 bit 0 ready after clock control writes (E0423) */
    if (offset == 0x30) {
        return 0x1;
    }
    return 0;
}

static void cxd90045_crcdc_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
}

static const MemoryRegionOps cxd90045_crcdc_ops = {
    .read = cxd90045_crcdc_read,
    .write = cxd90045_crcdc_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static uint64_t cxd90045_l2c_stub_read(void *opaque, hwaddr offset, unsigned size)
{
    /* Observed read stub 0xF800277C -> 0x100 */
    if (offset == 0x77C) {
        return 0x100;
    }
    return 0;
}

static void cxd90045_l2c_stub_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size)
{
}

static const MemoryRegionOps cxd90045_l2c_stub_ops = {
    .read = cxd90045_l2c_stub_read,
    .write = cxd90045_l2c_stub_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

typedef struct CxdRwStub {
    MemoryRegion mmio;
    uint32_t *regs;
    size_t num_regs;
} CxdRwStub;

static uint64_t cxd90045_rw_stub_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdRwStub *s = opaque;
    size_t idx = offset >> 2;
    if (idx < s->num_regs) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_rw_stub_write(void *opaque, hwaddr offset, uint64_t value,
                                   unsigned size)
{
    CxdRwStub *s = opaque;
    size_t idx = offset >> 2;
    if (idx < s->num_regs) {
        s->regs[idx] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_rw_stub_ops = {
    .read = cxd90045_rw_stub_read,
    .write = cxd90045_rw_stub_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static CxdRwStub *cxd90045_create_rw_stub(MemoryRegion *sysmem, const char *name,
                                         hwaddr base, size_t size)
{
    CxdRwStub *s = g_new0(CxdRwStub, 1);
    s->num_regs = size / sizeof(uint32_t);
    s->regs = g_new0(uint32_t, s->num_regs);
    memory_region_init_io(&s->mmio, NULL, &cxd90045_rw_stub_ops, s, name, size);
    memory_region_add_subregion(sysmem, base, &s->mmio);
    return s;
}

typedef struct CxdSugilite {
    MemoryRegion mmio;
    qemu_irq irq;
    uint32_t regs[0x10000 / 4];
} CxdSugilite;

static uint64_t cxd90045_sugilite_read(void *opaque, hwaddr offset,
                                       unsigned size)
{
    CxdSugilite *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_sugilite_write(void *opaque, hwaddr offset, uint64_t value,
                                    unsigned size)
{
    CxdSugilite *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset == 0x1000 || offset == 0x1800) {
        if (value) {
            if (offset == 0x1800) {
                static int cmd_log = 0;
                uint32_t addr = s->regs[0x1804 >> 2];
                uint32_t buf[16] = {0};
                cpu_physical_memory_read(addr, buf, sizeof(buf));
                if (buf[7] != 0x12345678 && cmd_log < 20) {
                    fprintf(stderr,
                            "[SUGI-RENDER #%d] addr=0x%08x val=0x%08x :",
                            cmd_log, addr, (unsigned)value);
                    for (int k = 0; k < 16; k++) {
                        fprintf(stderr, " %08x", buf[k]);
                    }
                    fprintf(stderr, "\n");
                    cmd_log++;
                }
            }
            s->regs[0x34 >> 2] |= 0x80000000U;
            qemu_irq_raise(s->irq);
        } else if (offset == 0x1000) {
            s->regs[0x34 >> 2] &= ~0x80000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    } else if (offset == 0x0C18) {
        if (value) {
            s->regs[0x34 >> 2] |= 0x40000000U;
            qemu_irq_raise(s->irq);
        } else {
            s->regs[0x34 >> 2] &= ~0x40000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    } else if (offset == 0x001C) {
        if (value) {
            s->regs[0x34 >> 2] |= 0x04000000U;
            qemu_irq_raise(s->irq);
        } else {
            s->regs[0x34 >> 2] &= ~0x04000000U;
            if ((s->regs[0x34 >> 2] & 0xC400000FU) == 0) {
                qemu_irq_lower(s->irq);
            }
        }
    }
}

static const MemoryRegionOps cxd90045_sugilite_ops = {
    .read = cxd90045_sugilite_read,
    .write = cxd90045_sugilite_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void cxd90045_create_sugilite(MemoryRegion *sysmem, hwaddr base,
                                     qemu_irq irq)
{
    CxdSugilite *s = g_new0(CxdSugilite, 1);
    s->irq = irq;
    memory_region_init_io(&s->mmio, NULL, &cxd90045_sugilite_ops, s,
                          "cxd90045.sugilite_bar", 0x10000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

typedef struct CxdLdec {
    MemoryRegion mmio;
    qemu_irq irq;
    uint32_t regs[0x100 >> 2];
    uint8_t *in_buf;
    size_t in_cap;
    size_t in_len;
} CxdLdec;

static CxdLdec *s_cxd_ldec;

static bool cxd90045_ldec_lz77_decode(const uint8_t *in, size_t in_len,
                                      uint8_t *out, size_t max_out,
                                      size_t *out_len_p)
{
    static const uint32_t lengths[16] = {
        3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 32, 64
    };
    size_t pos = 0;
    size_t out_len = 0;
    while (pos < in_len && out_len < max_out) {
        uint8_t kind = in[pos++];
        if (kind == 0x0f) {
            if (pos + 3 > in_len) {
                return false;
            }
            pos++; /* skip 1 byte */
            uint32_t count = in[pos] | ((uint32_t)in[pos + 1] << 8);
            pos += 2;
            if (pos + count > in_len || out_len + count > max_out) {
                return false;
            }
            memcpy(out + out_len, in + pos, count);
            pos += count;
            out_len += count;
        } else if (kind == 0xf0) {
            bool done = false;
            while (!done) {
                if (pos >= in_len) {
                    return false;
                }
                uint8_t flags = in[pos++];
                if (flags == 0) {
                    if (pos + 8 > in_len || out_len + 8 > max_out) {
                        return false;
                    }
                    memcpy(out + out_len, in + pos, 8);
                    pos += 8;
                    out_len += 8;
                    continue;
                }
                for (int bit = 0; bit < 8; bit++) {
                    if ((flags >> bit) & 1) {
                        if (pos + 2 > in_len) {
                            return false;
                        }
                        uint8_t a = in[pos++];
                        uint8_t b = in[pos++];
                        uint32_t dist = ((uint32_t)(a & 0x0f) << 8) | b;
                        if (dist == 0) {
                            done = true;
                            break;
                        }
                        uint32_t len = lengths[a >> 4];
                        if (dist > out_len || out_len + len > max_out) {
                            return false;
                        }
                        for (uint32_t k = 0; k < len; k++) {
                            out[out_len] = out[out_len - dist];
                            out_len++;
                        }
                    } else {
                        if (pos >= in_len || out_len >= max_out) {
                            return false;
                        }
                        out[out_len++] = in[pos++];
                    }
                }
            }
        } else {
            return false;
        }
    }
    *out_len_p = out_len;
    return true;
}

static void cxd90045_ldec_feed(uint32_t dsa_off, uint32_t ssa, uint32_t len)
{
    CxdLdec *s = s_cxd_ldec;
    if (!s || len == 0 || len > (16 * 1024 * 1024) || dsa_off > (16 * 1024 * 1024)) {
        return;
    }
    if (dsa_off == 0) {
        s->in_len = 0;
    }
    size_t need = (size_t)dsa_off + (size_t)len;
    if (need > s->in_cap) {
        s->in_cap = need < 65536 ? 65536 : need * 2;
        s->in_buf = g_realloc(s->in_buf, s->in_cap);
    }
    cpu_physical_memory_read(ssa, s->in_buf + dsa_off, len);
    if (need > s->in_len) {
        s->in_len = need;
    }
    uint32_t records = s->regs[0x0c >> 2];
    uint32_t dst_phys = s->regs[0x10 >> 2];
    size_t max_out = (records > 0 && records <= 4096) ? (size_t)records * 4096 : (16 * 1024 * 1024);
    if (dst_phys != 0) {
        uint8_t *out_buf = g_malloc0(max_out);
        size_t decoded = 0;
        bool ok = cxd90045_ldec_lz77_decode(s->in_buf, s->in_len, out_buf, max_out, &decoded);
        if (ok && decoded > 0) {
            static int ldec_ok = 0;
            if (ldec_ok < 5) {
                fprintf(stderr,
                        "[LDEC OK #%d] in=0x%zx -> dst=0x%x out=0x%zx (rec=%u)\n",
                        ldec_ok, s->in_len, dst_phys, decoded, records);
                ldec_ok++;
            }
            cpu_physical_memory_write(dst_phys, out_buf, decoded);
            s->in_len = 0;
            s->regs[0x34 >> 2] = 0x1;
            qemu_irq_raise(s->irq);
        }
        g_free(out_buf);
    }
}

static uint64_t cxd90045_ldec_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdLdec *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_ldec_write(void *opaque, hwaddr offset, uint64_t value,
                                unsigned size)
{
    CxdLdec *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset == 0x30) {
        s->regs[0x34 >> 2] &= ~(uint32_t)value;
        if ((s->regs[0x34 >> 2] & 0x7bU) == 0) {
            qemu_irq_lower(s->irq);
        }
    }
}

static const MemoryRegionOps cxd90045_ldec_ops = {
    .read = cxd90045_ldec_read,
    .write = cxd90045_ldec_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static uint64_t cxd90045_f2a01_read(void *opaque, hwaddr offset, unsigned size)
{
    uint32_t *regs = opaque;
    if (offset == 0x04) {
        return regs[0x04 >> 2] | 0x1U;
    }
    if (offset == 0x2c) {
        return regs[0x2c >> 2] | 0x2U;
    }
    if (offset == 0x30) {
        return 0;
    }
    if ((offset >> 2) < 0x400) {
        return regs[offset >> 2];
    }
    return 0;
}

static void cxd90045_f2a01_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
    uint32_t *regs = opaque;
    if (offset == 0x30) {
        return; /* self-clearing trigger */
    }
    if ((offset >> 2) < 0x400) {
        regs[offset >> 2] = (uint32_t)value;
    }
}

static const MemoryRegionOps cxd90045_f2a01_ops = {
    .read = cxd90045_f2a01_read,
    .write = cxd90045_f2a01_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void cxd90045_create_ldec(MemoryRegion *sysmem, hwaddr base, qemu_irq irq)
{
    CxdLdec *s = g_new0(CxdLdec, 1);
    s->irq = irq;
    s_cxd_ldec = s;
    memory_region_init_io(&s->mmio, NULL, &cxd90045_ldec_ops, s,
                          "cxd90045.ldec", 0x1000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

typedef struct CxdXdmac {
    MemoryRegion mmio;
    qemu_irq irqs[4];
    uint32_t regs[0x100 >> 2];
} CxdXdmac;

static uint64_t cxd90045_xdmac_read(void *opaque, hwaddr offset, unsigned size)
{
    CxdXdmac *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        return s->regs[idx];
    }
    return 0;
}

static void cxd90045_xdmac_write(void *opaque, hwaddr offset, uint64_t value,
                                 unsigned size)
{
    CxdXdmac *s = opaque;
    size_t idx = offset >> 2;
    if (idx < ARRAY_SIZE(s->regs)) {
        s->regs[idx] = (uint32_t)value;
    }
    if (offset >= 0x10 && offset < 0x10 + 4 * 0x30) {
        int ch = (int)((offset - 0x10) / 0x30);
        hwaddr ch_off = (offset - 0x10) % 0x30;
        hwaddr ch_base = 0x10 + (hwaddr)ch * 0x30;
        if (ch_off == 0x18 && value != 0) {
            uint32_t tbc = s->regs[(ch_base + 0x00) >> 2] + 1;
            uint32_t ssa = s->regs[(ch_base + 0x04) >> 2];
            uint32_t dsa = s->regs[(ch_base + 0x08) >> 2];
            if (dsa >= CXD90045_LDEC_IN_BASE &&
                dsa < CXD90045_LDEC_IN_BASE + CXD90045_LDEC_IN_SIZE) {
                cxd90045_ldec_feed((uint32_t)(dsa - CXD90045_LDEC_IN_BASE),
                                   ssa, tbc);
            } else if (tbc > 0 && tbc <= (16 * 1024 * 1024)) {
                uint8_t buf[4096];
                uint32_t rem = tbc;
                uint32_t off = 0;
                while (rem > 0) {
                    uint32_t chunk = rem > sizeof(buf) ? sizeof(buf) : rem;
                    cpu_physical_memory_read(ssa + off, buf, chunk);
                    cpu_physical_memory_write(dsa + off, buf, chunk);
                    off += chunk;
                    rem -= chunk;
                }
            }
            s->regs[(ch_base + 0x20) >> 2] = 0x8;
            qemu_irq_raise(s->irqs[ch]);
        } else if (ch_off == 0x20 && value == 0) {
            s->regs[(ch_base + 0x20) >> 2] = 0;
            qemu_irq_lower(s->irqs[ch]);
        }
    }
}

static const MemoryRegionOps cxd90045_xdmac_ops = {
    .read = cxd90045_xdmac_read,
    .write = cxd90045_xdmac_write,
    .endianness = DEVICE_NATIVE_ENDIAN,
};

static void cxd90045_create_xdmac(MemoryRegion *sysmem, hwaddr base,
                                  qemu_irq *irqs)
{
    CxdXdmac *s = g_new0(CxdXdmac, 1);
    for (int i = 0; i < 4; i++) {
        s->irqs[i] = irqs[i];
    }
    memory_region_init_io(&s->mmio, NULL, &cxd90045_xdmac_ops, s,
                          "cxd90045.xdmac", 0x10000);
    memory_region_add_subregion(sysmem, base, &s->mmio);
}

/*
 * Sony boot contract (boot/loadaddr.txt "0x00108000,0x00700000,0x00103000",
 * E0425): the raw kernel Image runs at physical 0x00108000 (TEXT_OFFSET),
 * the initrd sits at 0x00700000, and r2 points at a NUL-terminated
 * "kemco ..." parameter string at 0x00103000 instead of ATAGS/DTB.
 * setup_arch() checks strncmp(ptr, "kemco", 5) and parses from ptr+5.
 * QEMU's generic arm_load_kernel() cannot express this layout, so the
 * machine loads the images itself and enters the kernel on CPU reset.
 */
#define CXD90045_KERNEL_LOAD     0x00108000ULL
#define CXD90045_INITRD_LOAD     0x00700000ULL
#define CXD90045_INITRD_MAX      0x00800000ULL
#define CXD90045_BOOT_PARAMS     0x00103000ULL
#define CXD90045_BOOT_PARAMS_MAX (CXD90045_KERNEL_LOAD - CXD90045_BOOT_PARAMS)

/*
 * Boot stubs in the reserved first megabyte (memrsv=1M@0), standing in for
 * what the boot ROM / bootloader does before the kernel runs (E0522):
 *
 *   CPU0 (CXD90045_STUB0): program GICD_ITARGETSR8..63, then r0 = 0,
 *     r1 = machine id, r2 = kemco block, jump to the kernel.  Sony's
 *     gic_dist_init never writes SPI targets and its irq_select_affinity
 *     (0x6011feb0) only reads the ITARGETSR byte back into the affinity
 *     mask, so the targets must already be set when the kernel starts.
 *   CPU1..3 (CXD90045_SMPSTUB): WFE until cxd900x0_smp_init stores the
 *     physical cxd900x0_secondary_entry at 0xFE005010 + 4 * (cpu - 1)
 *     and issues SEV, then jump there.  The kernel's own park loop then
 *     waits for pen_release (boot_secondary).
 *
 * Encodings assembled with arm-linux-gnueabi-as (receipt in E0522).
 */
#define CXD90045_STUB0           0x00001000ULL
#define CXD90045_SMPSTUB         0x00001200ULL
#define CXD90045_SMP_MBOX        0xFE005010ULL

static const uint32_t cxd90045_stub0[15] = {
    0xe59f3024,             /* ldr r3, =0xF8001820 (ITARGETSR8)  */
    0xe59f4024,             /* ldr r4, =0xF8001900               */
    0xe28f602c,             /* add r6, pc, #0x2c (table @ +0x3c) */
    0xe4965004,             /* 1: ldr r5, [r6], #4               */
    0xe4835004,             /* str r5, [r3], #4                  */
    0xe1530004,             /* cmp r3, r4                        */
    0x3afffffb,             /* blo 1b                            */
    0xe3a00000,             /* mov r0, #0                        */
    0xe59f100c,             /* ldr r1, =CXD90045_MACHINE_ID      */
    0xe59f200c,             /* ldr r2, =CXD90045_BOOT_PARAMS     */
    0xe3a0f942,             /* mov pc, #0x108000 (kernel entry)  */
    0xf8001820,
    0xf8001900,
    CXD90045_MACHINE_ID,
    0x00103000,
};

static const uint32_t cxd90045_smpstub[11] = {
    0xee103fb0,             /* mrc p15, 0, r3, c0, c0, 5 (MPIDR) */
    0xe2033003,             /* and r3, r3, #3                    */
    0xe2433001,             /* sub r3, r3, #1                    */
    0xe59f2014,             /* ldr r2, =0xFE005010               */
    0xe0822103,             /* add r2, r2, r3, lsl #2            */
    0xe320f002,             /* 1: wfe                            */
    0xe5920000,             /* ldr r0, [r2]                      */
    0xe3500000,             /* cmp r0, #0                        */
    0x0afffffb,             /* beq 1b                            */
    0xe12fff10,             /* bx r0                             */
    0xfe005010,
};

/*
 * SPI target bytes for IRQ 32..255.  Per-CPU sources come from this kernel:
 * clockevent channels ch0/ch2/ch3/ch4 = IRQ 153/155/156/157 for CPU0..3
 * (table 0x604d8d80) and hwtimer0..3 = ch5..8 = IRQ 158..161
 * (hwtimer_devs 0x603c9354; hwtimerN serving CPU N is inferred, and agrees
 * with the a5000 real-camera GICD dump in Oein/a5000-emulator).  Every
 * other SPI targets CPU0 [assumption: no CXD90045 GICD dump yet].
 */
static void cxd90045_spi_targets(uint32_t *words, unsigned ncpu)
{
    uint8_t t[256 - 32];
    static const struct { int irq; unsigned cpu; } percpu[] = {
        { 153, 0 }, { 155, 1 }, { 156, 2 }, { 157, 3 },
        { 158, 0 }, { 159, 1 }, { 160, 2 }, { 161, 3 },
    };

    memset(t, 0x01, sizeof(t));
    for (size_t i = 0; i < ARRAY_SIZE(percpu); i++) {
        if (percpu[i].cpu < ncpu) {
            t[percpu[i].irq - 32] = 1u << percpu[i].cpu;
        }
    }
    for (int i = 0; i < 56; i++) {
        words[i] = cpu_to_le32(t[4 * i] | (t[4 * i + 1] << 8) |
                               (t[4 * i + 2] << 16) |
                               ((uint32_t)t[4 * i + 3] << 24));
    }
}

static void sony_a6400_write_stubs(unsigned ncpu)
{
    uint32_t blob0[15 + 56];
    uint32_t smp[11];

    for (int i = 0; i < 15; i++) {
        blob0[i] = cpu_to_le32(cxd90045_stub0[i]);
    }
    cxd90045_spi_targets(&blob0[15], ncpu);
    rom_add_blob_fixed("sony_a6400.stub0", blob0, sizeof(blob0),
                       CXD90045_STUB0);
    for (int i = 0; i < 11; i++) {
        smp[i] = cpu_to_le32(cxd90045_smpstub[i]);
    }
    rom_add_blob_fixed("sony_a6400.smpstub", smp, sizeof(smp),
                       CXD90045_SMPSTUB);
}

static void sony_a6400_cpu_reset(void *opaque)
{
    ARMCPU *cpu = opaque;
    CPUState *cs = CPU(cpu);

    cpu_reset(cs);
    if (cs->cpu_index == 0) {
        /*
         * A reset sends the secondaries back to the mailbox loop; a stale
         * entry left by the previous kernel must not release them early.
         * SRAM is zero at power-on, which is the state reproduced here.
         */
        uint32_t mbox[3] = { 0 };
        address_space_write(&address_space_memory, CXD90045_SMP_MBOX,
                            MEMTXATTRS_UNSPECIFIED, mbox, sizeof(mbox));
        cpu_set_pc(cs, CXD90045_STUB0);
    } else {
        cpu_set_pc(cs, CXD90045_SMPSTUB);
    }
}

static void sony_a6400_load_boot_contract(MachineState *machine,
                                          ARMCPU **cpus, unsigned ncpu)
{
    const char *append = machine->kernel_cmdline ? machine->kernel_cmdline : "";
    g_autofree char *params = NULL;
    ssize_t size;

    for (unsigned i = 0; i < ncpu; i++) {
        qemu_register_reset(sony_a6400_cpu_reset, cpus[i]);
    }
    sony_a6400_write_stubs(ncpu);
    if (!machine->kernel_filename) {
        return;
    }

    size = load_image_targphys(machine->kernel_filename, CXD90045_KERNEL_LOAD,
                               CXD90045_INITRD_LOAD - CXD90045_KERNEL_LOAD);
    if (size <= 0) {
        error_report("sony-a6400: could not load kernel '%s'",
                     machine->kernel_filename);
        exit(1);
    }

    if (machine->initrd_filename) {
        size = load_image_targphys(machine->initrd_filename,
                                   CXD90045_INITRD_LOAD, CXD90045_INITRD_MAX);
        if (size <= 0) {
            error_report("sony-a6400: could not load initrd '%s'",
                         machine->initrd_filename);
            exit(1);
        }
    }

    params = g_str_has_prefix(append, "kemco") ? g_strdup(append)
                                                : g_strdup_printf("kemco %s", append);
    if (strlen(params) + 1 > CXD90045_BOOT_PARAMS_MAX) {
        error_report("sony-a6400: kemco boot parameters too long");
        exit(1);
    }
    rom_add_blob_fixed("sony_a6400.kemco", params, strlen(params) + 1,
                       CXD90045_BOOT_PARAMS);
}

static void sony_a6400_init(MachineState *machine)
{
    MemoryRegion *sysmem = get_system_memory();
    MemoryRegion *ram0 = g_new(MemoryRegion, 1);
    MemoryRegion *ram1 = g_new(MemoryRegion, 1);
    MemoryRegion *sram = g_new(MemoryRegion, 1);
    MemoryRegion *crcdc_mmio = g_new(MemoryRegion, 1);
    MemoryRegion *l2c_mmio = g_new(MemoryRegion, 1);
    CxdRwStub *sugi_clk;
    DeviceState *a9mp;
    SysBusDevice *sbd;
    qemu_irq pic[CXD90045_NUM_IRQS];
    unsigned ncpu = machine->smp.cpus;
    ARMCPU *cpus[CXD90045_MAX_CPUS];
    DeviceState *timer;
    int i;

    for (unsigned c = 0; c < ncpu; c++) {
        Object *cpuobj = object_new(machine->cpu_type);
        object_property_set_bool(cpuobj, "has_el3", false, &error_fatal);
        if (object_property_find(cpuobj, "reset-cbar")) {
            object_property_set_int(cpuobj, "reset-cbar",
                                    CXD90045_PERIPH_BASE, &error_fatal);
        }
        /* secondaries run from reset too; they park in the mailbox stub */
        qdev_realize(DEVICE(cpuobj), NULL, &error_fatal);
        cpus[c] = ARM_CPU(cpuobj);
    }

    memory_region_init_ram(ram0, NULL, "sony_a6400.ram0",
                           CXD90045_RAM0_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM0_BASE, ram0);

    memory_region_init_ram(ram1, NULL, "sony_a6400.ram1",
                           CXD90045_RAM1_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_RAM1_BASE, ram1);

    memory_region_init_ram(sram, NULL, "sony_a6400.sram",
                           CXD90045_SRAM_SIZE, &error_fatal);
    memory_region_add_subregion(sysmem, CXD90045_SRAM_BASE, sram);

    a9mp = qdev_new(TYPE_A9MPCORE_PRIV);
    qdev_prop_set_uint32(a9mp, "num-cpu", ncpu);
    qdev_prop_set_uint32(a9mp, "num-irq", CXD90045_NUM_IRQS);
    sbd = SYS_BUS_DEVICE(a9mp);
    sysbus_realize_and_unref(sbd, &error_fatal);
    sysbus_mmio_map(sbd, 0, CXD90045_PERIPH_BASE);
    for (unsigned c = 0; c < ncpu; c++) {
        sysbus_connect_irq(sbd, c,
                           qdev_get_gpio_in(DEVICE(cpus[c]), ARM_CPU_IRQ));
        sysbus_connect_irq(sbd, c + ncpu,
                           qdev_get_gpio_in(DEVICE(cpus[c]), ARM_CPU_FIQ));
    }

    for (i = 0; i < CXD90045_NUM_IRQS - 32; i++) {
        pic[i] = qdev_get_gpio_in(a9mp, i);
    }

    pl011_create(CXD90045_UART0_BASE, pic[CXD90045_UART0_SPI], serial_hd(0));

    timer = qdev_new("cxd4115-timer");
    sysbus_realize_and_unref(SYS_BUS_DEVICE(timer), &error_fatal);
    sysbus_mmio_map(SYS_BUS_DEVICE(timer), 0, CXD90045_TIMER_BASE);
    for (i = 0; i < 16; i++) {
        sysbus_connect_irq(SYS_BUS_DEVICE(timer), i,
                           pic[CXD90045_TIMER_IRQ0 + i - 32]);
    }

    memory_region_init_io(crcdc_mmio, NULL, &cxd90045_crcdc_ops, NULL,
                          "cxd90045.crcdc", 0x1000);
    memory_region_add_subregion(sysmem, CXD90045_CRCDC_BASE, crcdc_mmio);

    memory_region_init_io(l2c_mmio, NULL, &cxd90045_l2c_stub_ops, NULL,
                          "cxd90045.l2c_stub", 0x1000);
    memory_region_add_subregion(sysmem, CXD90045_L2C_STUB_BASE, l2c_mmio);

    cxd90045_create_sugilite(sysmem, CXD90045_GPES_BAR_BASE,
                             pic[CXD90045_GPES_SPI]);

    sugi_clk = cxd90045_create_rw_stub(sysmem, "cxd90045.sugi_clk",
                                       CXD90045_SUGI_CLK_BASE, 0x10000);
    /* sugilite_clock_up_internal expects 0x15 at 0xF3400080 */
    sugi_clk->regs[0x80 >> 2] = 0x15;

    cxd90045_create_rw_stub(sysmem, "cxd90045.dfs_ctrl",
                            CXD90045_DFS_CTRL_BASE, 0x1000);

    cxd90045_create_xdmac(sysmem, CXD90045_XDMAC0_BASE,
                          &pic[CXD90045_XDMAC0_SPI_BASE]);
    cxd90045_create_xdmac(sysmem, CXD90045_XDMAC1_BASE,
                          &pic[CXD90045_XDMAC1_SPI_BASE]);
    cxd90045_create_ldec(sysmem, CXD90045_LDEC_BASE,
                         pic[CXD90045_LDEC_SPI]);

    {
        static const hwaddr f2a_bases[] = {
            CXD90045_F2A01_BASE, 0xF2A11000ULL, 0xF2A12000ULL,
            0xF2A21000ULL, 0xF2A22000ULL, 0xF2A30000ULL
        };
        for (size_t k = 0; k < ARRAY_SIZE(f2a_bases); k++) {
            uint32_t *regs = g_new0(uint32_t, 0x400);
            MemoryRegion *mmio = g_new(MemoryRegion, 1);
            memory_region_init_io(mmio, NULL, &cxd90045_f2a01_ops, regs,
                                  "cxd90045.f2a_clk", 0x1000);
            memory_region_add_subregion(sysmem, f2a_bases[k], mmio);
        }
    }

    {
        DeviceState *sdhci = qdev_new(TYPE_SYSBUS_SDHCI);
        DriveInfo *di = drive_get(IF_SD, 0, 0);
        BlockBackend *blk = di ? blk_by_legacy_dinfo(di) : NULL;
        qdev_prop_set_uint8(sdhci, "sd-spec-version", 3);
        qdev_prop_set_uint64(sdhci, "capareg", 0x05e830b0);
        sbd = SYS_BUS_DEVICE(sdhci);
        sysbus_realize_and_unref(sbd, &error_fatal);
        sysbus_mmio_map(sbd, 0, CXD90045_EMMC0_BASE);
        sysbus_connect_irq(sbd, 0, pic[CXD90045_EMMC0_SPI]);
        if (blk) {
            DeviceState *card = qdev_new(TYPE_EMMC);
            qdev_prop_set_uint64(card, "boot-partition-size", 4 * MiB);
            qdev_prop_set_uint8(card, "boot-config", 0x08);
            qdev_prop_set_drive_err(card, "drive", blk, &error_fatal);
            qdev_realize_and_unref(card, qdev_get_child_bus(sdhci, "sd-bus"),
                                   &error_fatal);
        }
    }

    sony_a6400_load_boot_contract(machine, cpus, ncpu);
}

static void sony_a6400_machine_init(MachineClass *mc)
{
    mc->desc = "Sony ILCE-6400 (BIONZ X / CXD90045)";
    mc->init = sony_a6400_init;
    mc->max_cpus = CXD90045_MAX_CPUS;
    mc->default_cpus = CXD90045_MAX_CPUS;
    mc->default_cpu_type = ARM_CPU_TYPE_NAME("cortex-a5");
    mc->default_ram_size = CXD90045_RAM0_SIZE;
    mc->ignore_memory_transaction_failures = true;
}

DEFINE_MACHINE("sony-a6400", sony_a6400_machine_init)
