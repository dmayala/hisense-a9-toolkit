package org.a9.gnssprobe;
import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;

// Starts while foreground, then returns to the integrated E Ink hold's app.
public final class Start extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        Intent intent = new Intent(this, Probe.class);
        intent.putExtra("seconds", Math.max(10, Math.min(2100, getIntent().getIntExtra("seconds", 90))));
        intent.putExtra("inject_time",getIntent().getBooleanExtra("inject_time",false));
        startForegroundService(intent);
        finish();
    }
}
