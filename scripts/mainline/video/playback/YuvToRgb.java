package org.a9.videoprobe;

import java.nio.ByteBuffer;

/** Limited-range BT.601 for this probe's SD fixture; accepts planar or interleaved chroma. */
final class YuvToRgb {
    private static int clamp(int value) { return Math.max(0, Math.min(255, value)); }

    static void convert(ByteBuffer y, ByteBuffer u, ByteBuffer v,
                        int yRow, int uRow, int vRow, int yPixel, int uPixel, int vPixel,
                        int left, int top, int width, int height, int[] argb) {
        int yBase = y.position(), uBase = u.position(), vBase = v.position();
        for (int row = 0; row < height; row++) {
            int sy = top + row;
            for (int col = 0; col < width; col++) {
                int sx = left + col;
                int luma = Math.max(0, (y.get(yBase + sy * yRow + sx * yPixel) & 255) - 16);
                int cb = (u.get(uBase + (sy / 2) * uRow + (sx / 2) * uPixel) & 255) - 128;
                int cr = (v.get(vBase + (sy / 2) * vRow + (sx / 2) * vPixel) & 255) - 128;
                int r = clamp((298 * luma + 409 * cr + 128) >> 8);
                int g = clamp((298 * luma - 100 * cb - 208 * cr + 128) >> 8);
                int b = clamp((298 * luma + 516 * cb + 128) >> 8);
                argb[row * width + col] = 0xff000000 | (r << 16) | (g << 8) | b;
            }
        }
    }
}
