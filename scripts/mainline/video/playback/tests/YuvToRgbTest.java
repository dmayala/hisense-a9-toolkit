package org.a9.videoprobe;

import java.nio.ByteBuffer;
import java.util.Arrays;

/** Focused conversion smoke: reference colors and Android Image stride/crop layouts. */
public final class YuvToRgbTest {
    private static void close(int pixel, int expected) {
        for (int shift : new int[]{0, 8, 16})
            if (Math.abs(((pixel >> shift) & 255) - ((expected >> shift) & 255)) > 2)
                throw new AssertionError(Integer.toHexString(pixel) + " != " + Integer.toHexString(expected));
        if ((pixel >>> 24) != 255) throw new AssertionError("alpha");
    }
    private static int solid(int y, int u, int v) {
        int[] out = new int[1];
        YuvToRgb.convert(ByteBuffer.wrap(new byte[]{(byte)y}), ByteBuffer.wrap(new byte[]{(byte)u}),
            ByteBuffer.wrap(new byte[]{(byte)v}), 1, 1, 1, 1, 1, 1, 0, 0, 1, 1, out);
        return out[0];
    }
    public static void main(String[] args) {
        close(solid(16, 128, 128), 0xff000000);
        close(solid(235, 128, 128), 0xffffffff);
        close(solid(0, 128, 128), 0xff000000);
        System.out.println("PASS limited-range black/white/clipping");
        close(solid(81, 90, 240), 0xffff0000);
        close(solid(145, 54, 34), 0xff00ff00);
        close(solid(41, 240, 110), 0xff0000ff);
        System.out.println("PASS BT.601 color vectors");

        byte[] y = {16, (byte)235, 0, 0, (byte)235, 16, 0, 0};
        int[] out = new int[4];
        YuvToRgb.convert(ByteBuffer.wrap(y), ByteBuffer.wrap(new byte[]{(byte)128}),
            ByteBuffer.wrap(new byte[]{(byte)128}), 4, 1, 1, 1, 1, 1, 0, 0, 2, 2, out);
        if (!Arrays.equals(out, new int[]{0xff000000, -1, -1, 0xff000000}))
            throw new AssertionError("padded luma rows");
        System.out.println("PASS planar padded rows");

        byte[] luma = new byte[32], uv = new byte[16];
        // Nonzero buffer positions, crop origin (2,2), interleaved UV with pixel stride2.
        luma[3 + 2 * 6 + 2] = 81; luma[3 + 2 * 6 + 3] = 81;
        luma[3 + 3 * 6 + 2] = 81; luma[3 + 3 * 6 + 3] = 81;
        uv[2 + 6 + 2] = 90; uv[3 + 6 + 2] = (byte)240;
        ByteBuffer yy = ByteBuffer.wrap(luma), uu = ByteBuffer.wrap(uv), vv = ByteBuffer.wrap(uv);
        yy.position(3); uu.position(2); vv.position(3);
        YuvToRgb.convert(yy, uu, vv, 6, 6, 6, 1, 2, 2, 2, 2, 2, 2, out);
        for (int pixel : out) close(pixel, 0xffff0000);
        System.out.println("PASS cropped interleaved chroma and buffer offsets");
        if (yy.position() != 3 || uu.position() != 2 || vv.position() != 3)
            throw new AssertionError("mutated plane positions");
        System.out.println("PASS input plane positions preserved");
    }
}
