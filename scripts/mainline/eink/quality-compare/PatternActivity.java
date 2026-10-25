package org.a9.einkcompare;

import android.app.Activity;
import android.os.Bundle;
import android.os.Build;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.Rect;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import java.util.HashMap;

/** One Activity throughout: changing patterns does not cause an app-focus clear. */
public class PatternActivity extends Activity {
    private Patterns patterns;
    private final BroadcastReceiver receiver = new BroadcastReceiver() {
        @Override public void onReceive(Context c, Intent i) { patterns.show(i.getStringExtra("screen")); }
    };
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON |
                            WindowManager.LayoutParams.FLAG_FULLSCREEN);
        getWindow().getDecorView().setSystemUiVisibility(5894); // stable layout + immersive fullscreen
        patterns = new Patterns(this);
        setContentView(patterns);
        IntentFilter filter = new IntentFilter("org.a9.einkcompare.SHOW");
        if (Build.VERSION.SDK_INT >= 33) registerReceiver(receiver, filter, Context.RECEIVER_EXPORTED);
        else registerReceiver(receiver, filter);
    }
    @Override public void onDestroy() { unregisterReceiver(receiver); super.onDestroy(); }
    private static class Patterns extends View {
        final HashMap<String, Bitmap> images = new HashMap<>();
        final Paint paint = new Paint(); // no filtering, dithering or antialiasing
        String screen = "white";
        float downY, shift;
        Patterns(Context c) {
            super(c);
            setLayerType(View.LAYER_TYPE_SOFTWARE, null);
            try {
                for (String name : new String[]{"white", "black", "ramp", "checker", "text", "scroll", "text-long"})
                    images.put(name, BitmapFactory.decodeStream(c.getAssets().open(name + ".png")));
            } catch (Exception e) { throw new RuntimeException(e); }
        }
        void show(String name) {
            if (!images.containsKey(name) || name.equals("text-long")) return;
            screen = name; shift = 0; invalidate();
        }
        @Override protected void onDraw(Canvas canvas) {
            canvas.drawColor(Color.WHITE);
            float scale = Math.min(getWidth()/824f, getHeight()/1648f);
            canvas.save();
            canvas.translate((getWidth()-824*scale)/2, (getHeight()-1648*scale)/2);
            canvas.scale(scale, scale);
            canvas.drawBitmap(images.get(screen), 0, 0, paint);
            if (screen.equals("text") && shift > 0) {
                canvas.save(); canvas.clipRect(120,160,704,1488);
                canvas.drawBitmap(images.get("text-long"),
                    new Rect(0,(int)shift,584,1328+(int)shift), new Rect(120,160,704,1488),paint);
                canvas.restore();
            }
            canvas.restore();
        }
        @Override public boolean onTouchEvent(MotionEvent event) {
            if (!screen.equals("text")) return true;
            if (event.getAction() == MotionEvent.ACTION_DOWN) downY = event.getY();
            if (event.getAction() == MotionEvent.ACTION_MOVE) {
                shift = Math.max(0,Math.min(264,(downY-event.getY())*1648/getHeight())); invalidate();
            }
            if (event.getAction() == MotionEvent.ACTION_UP && shift > 20) show("scroll");
            return true;
        }
    }
}
