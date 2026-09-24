/* Microbenchmark: upstream 32px column diff vs v1 edge scan vs v2 diff. */
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#define MIN(a, b) ((a) < (b) ? (a) : (b))
#define MAX(a, b) ((a) > (b) ? (a) : (b))
#define DIV_ROUND_UP(n, d) (((n) + (d) - 1) / (d))
#include "../qemu/include/ui/spice-damage.h"

static int W = 1920, H = 1080, BPP = 4;
static uint8_t *g, *m, *bm;
static long nboxes, npix;
static int do_copy;

static void emit(void *o, const SimpleSpiceRect *r)
{
    nboxes++;
    npix += (long)(r->right - r->left) * (r->bottom - r->top);
    if (do_copy) {
        /* what qemu_spice_create_one_update does: guest->mirror, mirror->bitmap */
        int bw = (r->right - r->left) * BPP;
        for (int y = r->top; y < r->bottom; y++) {
            memcpy(m + (size_t)y * W * BPP + r->left * BPP,
                   g + (size_t)y * W * BPP + r->left * BPP, bw);
            memcpy(bm + (size_t)(y - r->top) * bw,
                   m + (size_t)y * W * BPP + r->left * BPP, bw);
        }
    }
}

/* upstream qemu_spice_create_update() */
static void old_diff(const SimpleSpiceRect *d)
{
    static const int blksize = 32;
    int blocks = DIV_ROUND_UP(W, blksize);
    int *dirty_top = malloc(sizeof(int) * blocks);
    int y, yoff, x, xoff, blk, bw;
    for (blk = 0; blk < blocks; blk++) dirty_top[blk] = -1;
    for (y = d->top; y < d->bottom; y++) {
        yoff = y * W * BPP;
        for (x = d->left; x < d->right; x += blksize) {
            xoff = x * BPP; blk = x / blksize; bw = MIN(blksize, d->right - x);
            if (memcmp(g + yoff + xoff, m + yoff + xoff, bw * BPP) == 0) {
                if (dirty_top[blk] != -1) {
                    SimpleSpiceRect u = { dirty_top[blk], x, y, x + bw };
                    emit(NULL, &u); dirty_top[blk] = -1;
                }
            } else if (dirty_top[blk] == -1) {
                dirty_top[blk] = y;
            }
        }
    }
    for (x = d->left; x < d->right; x += blksize) {
        blk = x / blksize; bw = MIN(blksize, d->right - x);
        if (dirty_top[blk] != -1) {
            SimpleSpiceRect u = { dirty_top[blk], x, d->bottom, x + bw };
            emit(NULL, &u);
        }
    }
    free(dirty_top);
}

/* v1 qemu_spice_create_rect_update(): per-pixel edge scans */
static void v1_diff(const SimpleSpiceRect *rect)
{
    int bpp = BPP, stride = W * BPP;
    int top = -1, bottom = -1, left = rect->right, right = rect->left, x, y;
    for (y = rect->top; y < rect->bottom; y++) {
        uint8_t *gg = g + y * stride, *mm = m + y * stride;
        if (memcmp(gg + rect->left * bpp, mm + rect->left * bpp,
                   (rect->right - rect->left) * bpp) == 0) continue;
        if (top != -1 && y - bottom >= 32) {
            SimpleSpiceRect u = { top, left, bottom, right }; emit(NULL, &u);
            top = -1; left = rect->right; right = rect->left;
        }
        if (top == -1) top = y;
        bottom = y + 1;
        for (x = rect->left; x < left; x++)
            if (memcmp(gg + x * bpp, mm + x * bpp, bpp) != 0) { left = x; break; }
        for (x = rect->right - 1; x >= right; x--)
            if (memcmp(gg + x * bpp, mm + x * bpp, bpp) != 0) { right = x + 1; break; }
    }
    if (top != -1) { SimpleSpiceRect u = { top, left, bottom, right }; emit(NULL, &u); }
}

static void v2_diff(const SimpleSpiceRect *r)
{
    simple_spice_damage_diff(r, BPP, g, W * BPP, m, W * BPP, 32, 0, emit, NULL);
}

static void v3_diff(const SimpleSpiceRect *r)
{
    simple_spice_damage_diff(r, BPP, g, W * BPP, m, W * BPP, 32, 32, emit, NULL);
}

static double now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec + t.tv_nsec / 1e9;
}

static void px(int x, int y) { g[((size_t)y * W + x) * BPP] ^= 0x55; }

static void setup(const char *sc)
{
    memset(g, 0x20, (size_t)W * H * BPP);
    memset(m, 0x20, (size_t)W * H * BPP);
    if (!strcmp(sc, "full")) {
        for (size_t i = 0; i < (size_t)W * H * BPP; i++) g[i] = i * 7 + 1;
    } else if (!strcmp(sc, "none")) {
    } else if (!strcmp(sc, "stripe")) {
        for (int y = 0; y < H; y++) px(W / 2, y);
    } else if (!strcmp(sc, "lastcol")) {
        for (int y = 0; y < H; y++) px(W - 1, y);
    } else if (!strcmp(sc, "firstcol")) {
        for (int y = 0; y < H; y++) px(0, y);
    } else if (!strcmp(sc, "corner")) {
        px(W - 1, H - 1);
    } else if (!strcmp(sc, "video")) {
        for (int y = 360; y < 720; y++) for (int x = 720; x < 1200; x++) px(x, y);
    } else if (!strcmp(sc, "sparse")) {
        srand(1); for (int y = 0; y < H; y++) px(rand() % W, y);
    } else if (!strcmp(sc, "vidclock")) {
        for (int y = 360; y < 720; y++) for (int x = 720; x < 1200; x++) px(x, y);
        for (int y = 400; y < 428; y++) for (int x = 1400; x < 1480; x++) px(x, y);
    } else if (!strcmp(sc, "twocols")) {
        for (int y = 0; y < H; y++) { px(W / 3, y); px(2 * W / 3, y); }
    }
}

int main(int argc, char **argv)
{
    static const char *scs[] = { "full", "none", "video", "vidclock", "stripe", "firstcol",
                                 "lastcol", "twocols", "sparse", "corner" };
    int iters = argc > 1 ? atoi(argv[1]) : 200;
    do_copy = argc > 2 ? atoi(argv[2]) : 0;
    g = aligned_alloc(64, (size_t)W * H * BPP);
    m = aligned_alloc(64, (size_t)W * H * BPP);
    bm = aligned_alloc(64, (size_t)W * H * BPP);
    SimpleSpiceRect all = { 0, 0, H, W };
    printf("%-9s %9s %9s %9s %9s   boxes(old/v1/v2/v2+cols)\n", "scenario", "old us", "v1 us", "v2 us", "v2+cols");
    for (unsigned s = 0; s < sizeof(scs) / sizeof(*scs); s++) {
        void (*fn[4])(const SimpleSpiceRect *) = { old_diff, v1_diff, v2_diff, v3_diff };
        double us[4]; long nb[4];
        for (int f = 0; f < 4; f++) {
            double best = 1e9;
            setup(scs[s]);
            for (int rep = 0; rep < 5; rep++) {
                double t0, t = 0;
                nboxes = 0;
                for (int i = 0; i < iters; i++) {
                    if (do_copy) {
                        setup(scs[s]);
                    }
                    t0 = now(); fn[f](&all); t += now() - t0;
                }
                best = MIN(best, t / iters * 1e6);
            }
            us[f] = best; nb[f] = nboxes / iters;
        }
        printf("%-9s %9.1f %9.1f %9.1f %9.1f   %ld/%ld/%ld/%ld\n", scs[s], us[0], us[1], us[2], us[3], nb[0], nb[1], nb[2], nb[3]);
    }
    return 0;
}
