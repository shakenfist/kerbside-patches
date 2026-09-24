/*
 * Minimal static PID 1 for a SPICE damage-tracking workload.
 *
 * Loads virtio-gpu, takes KMS master on /dev/dri/card0, paints a static
 * "text" background once, then animates a video-like (smooth plasma)
 * sub-rectangle at a fixed frame rate, flushing it with DIRTYFB clip
 * rects. A small "clock" rect elsewhere changes once a second, so the
 * two damage sources are far apart on screen.
 *
 * Kernel cmdline knobs: anim.fps=N anim.w=W anim.h=H
 */
#define _GNU_SOURCE
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/mman.h>
#include <sys/mount.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <time.h>
#include <unistd.h>
#include <drm/drm.h>
#include <drm/drm_mode.h>
#include <linux/fb.h>

static const char *mods[] = {
    "/m/virtio_dma_buf.ko.xz", "/m/drm.ko.xz", "/m/drm_kms_helper.ko.xz",
    "/m/drm_shmem_helper.ko.xz", "/m/virtio-gpu.ko.xz", NULL,
};

static uint32_t *fb;
static uint32_t pitch_px, W, H, fb_id;
static int dfd = -1;

#define MAX_HEADS 2
struct head {
    uint32_t *fb, pitch_px, fb_id;
};
static struct head heads[MAX_HEADS];
static int nheads = 1;

static void die(const char *m)
{
    printf("ANIM FATAL: %s: %s\n", m, strerror(errno));
    fflush(stdout);
    for (;;) {
        pause();
    }
}

static void dirty(uint32_t x, uint32_t y, uint32_t w, uint32_t h)
{
    if (dfd < 0) {
        return; /* fbdev: the device sees the writes itself */
    }
    struct drm_clip_rect c = { x, y, x + w, y + h };
    struct drm_mode_fb_dirty_cmd d = {
        .fb_id = fb_id, .num_clips = 1, .clips_ptr = (uintptr_t)&c,
    };
    if (ioctl(dfd, DRM_IOCTL_MODE_DIRTYFB, &d) < 0) {
        die("DIRTYFB");
    }
}

static int param(const char *cmdline, const char *key, int def)
{
    const char *p = strstr(cmdline, key);
    return p ? atoi(p + strlen(key)) : def;
}

/* 3x5 digit font for the clock */
static const uint16_t digits[10] = {
    0x7b6f, 0x2492, 0x73e7, 0x73cf, 0x5bc9, 0x79cf, 0x79ef, 0x7249,
    0x7bef, 0x7bcf,
};

static void draw_clock(uint32_t cx, uint32_t cy, unsigned v)
{
    const uint32_t s = 4, cw = 5 * 4 * s, ch = 7 * s;
    for (uint32_t y = cy; y < cy + ch; y++) {
        for (uint32_t x = cx; x < cx + cw; x++) {
            fb[y * pitch_px + x] = 0x00202020;
        }
    }
    for (int i = 0; i < 5; i++) {
        unsigned d = v % 10;
        v /= 10;
        for (int r = 0; r < 5; r++) {
            for (int c = 0; c < 3; c++) {
                if (!(digits[d] >> (14 - (r * 3 + c)) & 1)) {
                    continue;
                }
                uint32_t x0 = cx + (4 - i) * 4 * s + c * s, y0 = cy + s + r * s;
                for (uint32_t y = y0; y < y0 + s; y++) {
                    for (uint32_t x = x0; x < x0 + s; x++) {
                        fb[y * pitch_px + x] = 0x0000ff00;
                    }
                }
            }
        }
    }
    dirty(cx, cy, cw, ch);
}

static void use_head(int h)
{
    fb = heads[h].fb;
    pitch_px = heads[h].pitch_px;
    fb_id = heads[h].fb_id;
}

static void setup_fbdev(void)
{
    struct fb_var_screeninfo var;
    struct fb_fix_screeninfo fix;
    int fd = -1;

    for (int i = 0; i < 100 && (fd = open("/dev/fb0", O_RDWR)) < 0; i++) {
        usleep(50000);
    }
    if (fd < 0) {
        die("open fb0");
    }
    if (ioctl(fd, FBIOGET_VSCREENINFO, &var) < 0 ||
        ioctl(fd, FBIOGET_FSCREENINFO, &fix) < 0) {
        die("FBIOGET");
    }
    printf("ANIM fbdev %ux%u bpp %u line %u\n", var.xres, var.yres,
           var.bits_per_pixel, fix.line_length);
    if (var.bits_per_pixel != 32) {
        errno = EINVAL;
        die("fbdev bpp");
    }
    W = var.xres;
    H = var.yres;
    heads[0].pitch_px = fix.line_length / 4;
    heads[0].fb = mmap(NULL, fix.smem_len, PROT_READ | PROT_WRITE,
                       MAP_SHARED, fd, 0);
    if (heads[0].fb == MAP_FAILED) {
        die("mmap fb0");
    }
    nheads = 1;
}

static void setup_drm(void)
{
    int fd;

    for (int i = 0; mods[i]; i++) {
        fd = open(mods[i], O_RDONLY);
        if (fd < 0 || syscall(SYS_finit_module, fd, "", 4) < 0) {
            die(mods[i]);
        }
        close(fd);
    }
    for (int i = 0; i < 100 && (dfd = open("/dev/dri/card0", O_RDWR)) < 0; i++) {
        usleep(50000);
    }
    if (dfd < 0) {
        die("open card0");
    }

    uint32_t conns[8], crtcs[8], encs[8];
    struct drm_mode_card_res res = { 0 };
    ioctl(dfd, DRM_IOCTL_MODE_GETRESOURCES, &res);
    res.connector_id_ptr = (uintptr_t)conns;
    res.crtc_id_ptr = (uintptr_t)crtcs;
    res.encoder_id_ptr = (uintptr_t)encs;
    res.count_fbs = 0;
    if (ioctl(dfd, DRM_IOCTL_MODE_GETRESOURCES, &res) < 0) {
        die("GETRESOURCES");
    }
    if (nheads > (int)res.count_connectors) {
        nheads = res.count_connectors;
    }
    if (nheads > (int)res.count_crtcs) {
        nheads = res.count_crtcs;
    }
    struct drm_mode_modeinfo mode0;
    for (int h = 0; h < nheads; h++) {
        struct drm_mode_modeinfo modes[32];
        struct drm_mode_get_connector conn = { .connector_id = conns[h] };
        ioctl(dfd, DRM_IOCTL_MODE_GETCONNECTOR, &conn);
        conn.count_props = 0;
        conn.count_encoders = 0;
        conn.count_modes = conn.count_modes > 32 ? 32 : conn.count_modes;
        conn.modes_ptr = (uintptr_t)modes;
        if (ioctl(dfd, DRM_IOCTL_MODE_GETCONNECTOR, &conn) < 0) {
            die("GETCONNECTOR");
        }
        if (!conn.count_modes) {
            if (h == 0) {
                die("no modes");
            }
            modes[0] = mode0;   /* disconnected head: force head 0's mode */
        }
        if (h == 0) {
            mode0 = modes[0];
            W = modes[0].hdisplay;
            H = modes[0].vdisplay;
        }
        struct drm_mode_create_dumb cd = { .width = W, .height = H, .bpp = 32 };
        if (ioctl(dfd, DRM_IOCTL_MODE_CREATE_DUMB, &cd) < 0) {
            die("CREATE_DUMB");
        }
        struct drm_mode_fb_cmd fbc = {
            .width = W, .height = H, .pitch = cd.pitch, .bpp = 32, .depth = 24,
            .handle = cd.handle,
        };
        if (ioctl(dfd, DRM_IOCTL_MODE_ADDFB, &fbc) < 0) {
            die("ADDFB");
        }
        heads[h].fb_id = fbc.fb_id;
        struct drm_mode_map_dumb md = { .handle = cd.handle };
        if (ioctl(dfd, DRM_IOCTL_MODE_MAP_DUMB, &md) < 0) {
            die("MAP_DUMB");
        }
        heads[h].fb = mmap(NULL, cd.size, PROT_READ | PROT_WRITE, MAP_SHARED,
                           dfd, md.offset);
        if (heads[h].fb == MAP_FAILED) {
            die("mmap");
        }
        heads[h].pitch_px = cd.pitch / 4;
        struct drm_mode_crtc crtc = {
            .crtc_id = crtcs[h], .fb_id = fbc.fb_id,
            .set_connectors_ptr = (uintptr_t)&conns[h],
            .count_connectors = 1, .mode = modes[0], .mode_valid = 1,
        };
        if (ioctl(dfd, DRM_IOCTL_MODE_SETCRTC, &crtc) < 0) {
            die("SETCRTC");
        }
    }
}

int main(void)
{
    char cmdline[1024] = "";
    int fd;

    mkdir("/dev", 0755);
    mkdir("/proc", 0755);
    mount("devtmpfs", "/dev", "devtmpfs", 0, NULL);
    mount("proc", "/proc", "proc", 0, NULL);
    fd = open("/dev/console", O_RDWR);
    if (fd >= 0) {
        dup2(fd, 1);
        dup2(fd, 2);
    }
    fd = open("/proc/cmdline", O_RDONLY);
    if (fd >= 0) {
        read(fd, cmdline, sizeof(cmdline) - 1);
        close(fd);
    }
    int fps = param(cmdline, "anim.fps=", 30);
    uint32_t vw = param(cmdline, "anim.w=", 480);
    uint32_t vh = param(cmdline, "anim.h=", 360);
    int delay = param(cmdline, "anim.delay=", 0);
    int fbdev = param(cmdline, "anim.fbdev=", 0);
    nheads = param(cmdline, "anim.heads=", 1);
    if (nheads < 1 || nheads > MAX_HEADS) {
        nheads = 1;
    }

    if (fbdev) {
        setup_fbdev();
    } else {
        setup_drm();
    }
    int cx = param(cmdline, "anim.cx=", 24);
    int cy = param(cmdline, "anim.cy=", H - 60);

    for (int h = 0; h < nheads; h++) {
        use_head(h);
        /* Static background: pseudo-text glyph cells, 8x16, on dark blue. */
        uint32_t seed = 12345;
        for (uint32_t gy = 0; gy < H / 16; gy++) {
            for (uint32_t gx = 0; gx < W / 8; gx++) {
                seed = seed * 1103515245 + 12345;
                int blank = (seed >> 16) % 5 == 0;
                for (uint32_t y = 0; y < 16; y++) {
                    seed = seed * 1103515245 + 12345;
                    uint8_t bits = blank || y < 3 || y > 13 ? 0 : (seed >> 16) & 0x7e;
                    for (uint32_t x = 0; x < 8; x++) {
                        fb[(gy * 16 + y) * pitch_px + gx * 8 + x] =
                            (bits >> x) & 1 ? 0x00c0c0c0 : 0x00102040;
                    }
                }
            }
        }
        dirty(0, 0, W, H);
    }
    sleep(delay);

    uint32_t vx = (W - vw) / 2 + 37, vy = (H - vh) / 2 + 21;
    printf("ANIM START %ux%u video %ux%u@%u,%u clock@%d,%d heads=%d fbdev=%d fps=%d\n",
           W, H, vw, vh, vx, vy, cx, cy, nheads, fbdev, fps);
    fflush(stdout);

    float *st = malloc(sizeof(float) * 1024);
    for (int i = 0; i < 1024; i++) {
        st[i] = sinf(i * 2 * (float)M_PI / 1024);
    }
    struct timespec next;
    clock_gettime(CLOCK_MONOTONIC, &next);
    long period = 1000000000L / fps;
    for (unsigned f = 0;; f++) {
        for (int h = 0; h < nheads; h++) {
            use_head(h);
            unsigned ff = f + h * 100;
            for (uint32_t y = 0; y < vh; y++) {
                uint32_t *row = fb + (vy + y) * pitch_px + vx;
                float a = st[(y * 3 + ff * 7) & 1023];
                for (uint32_t x = 0; x < vw; x++) {
                    float v = st[(x * 2 + ff * 5) & 1023] + a +
                              st[((x + y) * 2 + ff * 11) & 1023];
                    int r = 128 + 42 * v, g = 128 + 42 * st[((int)(v * 170) + ff * 3) & 1023];
                    int b = 128 - 42 * v;
                    row[x] = (r & 0xff) << 16 | (g & 0xff) << 8 | (b & 0xff);
                }
            }
            dirty(vx, vy, vw, vh);
            if (f % fps == 0) {
                draw_clock(cx, cy, f / fps);
            }
        }
        next.tv_nsec += period;
        while (next.tv_nsec >= 1000000000L) {
            next.tv_nsec -= 1000000000L;
            next.tv_sec++;
        }
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &next, NULL);
    }
}
