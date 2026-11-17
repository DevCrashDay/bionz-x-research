/* Bounded v2 package reader for the E0522 FAT32 layout. */
#define _DEFAULT_SOURCE
#define _FILE_OFFSET_BITS 64
#include <errno.h>
#include <fcntl.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/sysmacros.h>
#include <unistd.h>

enum { SECTOR = 512, PARTITION = 8192, PARTITION_SECTORS = 15556608,
       FAT = 0x112a, DATA = FAT + 2 * 0x76b, SPC = 64,
       CLUSTER = SECTOR * SPC, MAX_CLUSTER = (PARTITION_SECTORS - DATA) / SPC + 1 };
#define END_CLUSTER 0x0ffffff8u
#ifndef STAGE
#define STAGE "/tmp_bt/mlv2"
#endif

struct entry { uint32_t cluster, bytes; unsigned directory; };
static unsigned char chunk[CLUSTER];

static uint32_t le16(const unsigned char *p) { return p[0] | (uint32_t)p[1] << 8; }
static uint32_t le32(const unsigned char *p) { return le16(p) | le16(p + 2) << 16; }

static int read_at(int fd, void *out, uint32_t lba, size_t size) {
    if (lba >= PARTITION_SECTORS || size > CLUSTER ||
        size > (uint64_t)(PARTITION_SECTORS - lba) * SECTOR) return -1;
    return pread(fd, out, size, (off_t)lba * SECTOR) == (ssize_t)size ? 0 : -1;
}

static int next_cluster(int fd, uint32_t *cluster) {
    unsigned char sector[SECTOR];
    uint32_t index = *cluster * 4;
    if (read_at(fd, sector, FAT + index / SECTOR, sizeof(sector))) return -1;
    *cluster = le32(sector + index % SECTOR) & 0x0fffffffu;
    return 0;
}

static int valid_cluster(uint32_t cluster) { return cluster >= 2 && cluster <= MAX_CLUSTER; }

static uint32_t cluster_lba(uint32_t cluster) { return DATA + (cluster - 2) * SPC; }

static int find(int fd, uint32_t cluster, const char *wanted, struct entry *match) {
    char lfn[66] = {0};
    unsigned remaining = 0, sequence = 0;
    unsigned char checksum = 0;
    static const unsigned offsets[] = {1,3,5,7,9,14,16,18,20,22,24,28,30};
    for (unsigned block = 0; cluster < END_CLUSTER && block < 4; block++) {
        if (!valid_cluster(cluster) || read_at(fd, chunk, cluster_lba(cluster), sizeof(chunk))) return -1;
        for (unsigned off = 0; off < sizeof(chunk); off += 32) {
            const unsigned char *e = chunk + off;
            if (!e[0]) return 0;
            if (e[0] == 0xe5) { sequence = 0; continue; }
            if (e[11] == 15) {
                unsigned order = e[0] & 31;
                if (e[0] & 64) {
                    sequence = remaining = order <= 5 ? order : 0;
                    checksum = e[13];
                    memset(lfn, 0, sizeof(lfn));
                }
                if (!order || order != remaining || e[13] != checksum) { sequence = 0; continue; }
                for (unsigned i = 0; i < 13; i++) {
                    uint32_t c = le16(e + offsets[i]);
                    lfn[(order - 1) * 13 + i] = c == 0xffff ? 0 : c <= 127 ? (char)c : '?';
                }
                remaining--;
                continue;
            }
            unsigned char actual = 0;
            for (unsigned i = 0; i < 11; i++)
                actual = (unsigned char)(((actual & 1 ? 128 : 0) | actual >> 1) + e[i]);
            char short_name[13];
            unsigned n = 0;
            for (unsigned i = 0; i < 8 && e[i] != ' '; i++) short_name[n++] = (char)e[i];
            if (e[8] != ' ') {
                short_name[n++] = '.';
                for (unsigned i = 8; i < 11 && e[i] != ' '; i++) short_name[n++] = (char)e[i];
            }
            short_name[n] = 0;
            const char *name = sequence && !remaining && actual == checksum ? lfn : short_name;
            sequence = 0;
            if (!(e[11] & 8) && !strcasecmp(name, wanted)) {
                match->cluster = le16(e + 20) << 16 | le16(e + 26);
                match->bytes = le32(e + 28);
                match->directory = !!(e[11] & 16);
                return 1;
            }
        }
        if (next_cluster(fd, &cluster)) return -1;
    }
    return cluster >= END_CLUSTER ? 0 : -1;
}

static int extract(int fd, struct entry item) {
    int out = open(STAGE "/card.tar", O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW, 0600);
    if (out < 0) return -1;
    int result = 0;
    while (item.bytes) {
        size_t size = item.bytes < CLUSTER ? item.bytes : CLUSTER;
        if (!valid_cluster(item.cluster) || read_at(fd, chunk, cluster_lba(item.cluster), size) ||
            write(out, chunk, size) != (ssize_t)size) {
            result = -1;
            break;
        }
        item.bytes -= size;
        if (item.bytes && next_cluster(fd, &item.cluster)) { result = -1; break; }
    }
    if (close(out)) result = -1;
    if (result) unlink(STAGE "/card.tar");
    return result;
}

static int geometry(int fd) {
    unsigned char sector[SECTOR];
    return read_at(fd, sector, 0, sizeof(sector)) || le16(sector + 510) != 0xaa55 ||
           le16(sector + 11) != SECTOR || sector[13] != SPC || le16(sector + 14) != 0x112a ||
           sector[16] != 2 || le32(sector + 28) != PARTITION || le32(sector + 32) != PARTITION_SECTORS ||
           le32(sector + 36) != 0x76b || le32(sector + 44) != 2;
}

/* The pinned archive is validated by the startup worker before extraction. */
static int package(int fd, struct entry *archive) {
    struct entry hit;
    if (geometry(fd) ||
        find(fd, 2, "disable_hook", &hit) ||
        find(fd, 2, "disable.txt", &hit) ||
        find(fd, 2, "MLV2.OFF", &hit) ||
        find(fd, 2, "MLV2.TAR", archive) != 1 ||
        archive->directory || !archive->bytes || archive->bytes > 1024u * 1024u)
        return -1;
    return 0;
}

int main(int argc, char **argv) {
    int presence_only = argc == 2 && !strcmp(argv[1], "--present");
    if (argc != 1 && !presence_only) return 2;
    int fd = open("/dev/mmca1", O_RDONLY | O_NOFOLLOW | O_NONBLOCK);
    if (fd < 0) return 1;
    struct stat info;
    struct entry archive;
    int result = 1;
    if (!fstat(fd, &info) && S_ISBLK(info.st_mode) &&
        major(info.st_rdev) == 250 && minor(info.st_rdev) == 11 && !package(fd, &archive))
        result = presence_only ? 0 : (extract(fd, archive) ? 2 : 0);
    close(fd);
    return result;
}
