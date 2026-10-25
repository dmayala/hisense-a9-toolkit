package org.a9.gnssprobe;
import android.Manifest;
import android.app.*;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.location.*;
import android.os.*;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;

// LocationManager observer: bounded app-private satellite diagnostics, never
// coordinates, Location serialization, NMEA or locations in logcat.
public final class Probe extends Service {
    private final Handler handler = new Handler(Looper.getMainLooper());
    private LocationManager manager;
    private boolean started;
    private int fixes,svReports,svMax,svSignalMax,svUsedMax,eventBytes;
    private float cn0Max;
    private boolean forceTime;
    private void event(String line) {
        byte[] bytes=("elapsed_ms="+SystemClock.elapsedRealtime()+" "+line+"\n").getBytes(StandardCharsets.US_ASCII);
        if(eventBytes+bytes.length>262144) return;
        try (FileOutputStream out=openFileOutput("events",MODE_APPEND)) {
            out.write(bytes); eventBytes+=bytes.length;
        } catch(Exception ignored) { }
    }
    private final GnssStatus.Callback satelliteListener = new GnssStatus.Callback() {
        @Override public void onStarted() { event("provider_session=started"); }
        @Override public void onStopped() { event("provider_session=stopped"); }
        @Override public void onFirstFix(int ttff) { event("first_fix_ttff_ms="+ttff); }
        @Override public void onSatelliteStatusChanged(GnssStatus status) {
            ++svReports; int count=status.getSatelliteCount(),signal=0,used=0;
            svMax=Math.max(svMax,count);
            boolean detail=svReports<=5 || svReports%30==0;
            if(detail) event("sv_report="+svReports+" count="+count);
            for(int i=0;i<count;++i) {
                float cn0=status.getCn0DbHz(i);
                if(cn0>0) ++signal;
                if(status.usedInFix(i)) ++used;
                cn0Max=Math.max(cn0Max,cn0);
                if(detail) event("sv constellation="+status.getConstellationType(i)+" svid="+status.getSvid(i)+
                    " cn0_dbhz="+cn0+" used="+status.usedInFix(i)+" ephemeris="+status.hasEphemerisData(i)+
                    " almanac="+status.hasAlmanacData(i));
            }
            svSignalMax=Math.max(svSignalMax,signal); svUsedMax=Math.max(svUsedMax,used);
            save("satellites");
        }
    };
    private String reason = "created";
    private final LocationListener listener = new LocationListener() {
        @Override public void onLocationChanged(Location ignored) { ++fixes; save("receiving"); }
        @Override public void onProviderEnabled(String ignored) { save("enabled"); }
        @Override public void onProviderDisabled(String ignored) { save("disabled"); }
    };
    private void save(String value) {
        reason = value;
        try (FileOutputStream out = openFileOutput("summary", MODE_PRIVATE)) {
            out.write(("GNSS_PROBE fixes=" + fixes + " sv_reports="+svReports+" sv_max="+svMax+" sv_with_cn0_max="+svSignalMax+" cn0_max="+cn0Max+" sv_used_max="+svUsedMax+" inject_time_requested="+forceTime+" state=" + reason + "\n").getBytes(StandardCharsets.US_ASCII));
        } catch (Exception ignored) { /* Never log exception data. */ }
    }
    @Override public void onCreate() {
        super.onCreate();
        NotificationManager nm = getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("gnss", "GNSS validation", NotificationManager.IMPORTANCE_LOW));
        Notification notification = new Notification.Builder(this, "gnss")
            .setSmallIcon(android.R.drawable.ic_menu_mylocation)
            .setContentTitle("A9 GNSS validation").setContentText("Satellite diagnostics; no coordinates").build();
        startForeground(1, notification);
        manager = getSystemService(LocationManager.class);
    }
    @Override public int onStartCommand(Intent intent, int flags, int id) {
        if (started) return START_NOT_STICKY;
        started = true;
        try (FileOutputStream out=openFileOutput("events",MODE_PRIVATE)) { out.write(new byte[0]); }
        catch(Exception ignored) { }
        forceTime=intent!=null && intent.getBooleanExtra("inject_time",false);
        event("probe_start force_time="+forceTime);
        int seconds = intent == null ? 90 : Math.max(10, Math.min(2100, intent.getIntExtra("seconds",90)));
        handler.postDelayed(() -> { save("timeout"); stopSelf(); }, seconds * 1000L);
        if (checkSelfPermission(Manifest.permission.ACCESS_FINE_LOCATION) != PackageManager.PERMISSION_GRANTED ||
            checkSelfPermission(Manifest.permission.ACCESS_COARSE_LOCATION) != PackageManager.PERMISSION_GRANTED) {
            save("permission_missing"); stopSelf(); return START_NOT_STICKY;
        }
        try {
            if (!manager.isProviderEnabled(LocationManager.GPS_PROVIDER)) {
                save("provider_disabled"); stopSelf(); return START_NOT_STICKY;
            }
            manager.registerGnssStatusCallback(satelliteListener,handler);
            manager.requestLocationUpdates(LocationManager.GPS_PROVIDER, 1000L, 0.0f, listener, Looper.getMainLooper());
            if(forceTime) {
                boolean accepted=manager.sendExtraCommand(LocationManager.GPS_PROVIDER,"force_time_injection",new Bundle());
                event("framework_force_time_command_accepted="+accepted);
            }
            save("listening");
        } catch (RuntimeException ignored) { save("request_failed"); stopSelf(); }
        return START_NOT_STICKY;
    }
    @Override public void onDestroy() {
        handler.removeCallbacksAndMessages(null);
        if (manager != null) { manager.removeUpdates(listener); manager.unregisterGnssStatusCallback(satelliteListener); }
        save("stopped_" + reason);
        stopForeground(STOP_FOREGROUND_REMOVE);
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent ignored) { return null; }
}
