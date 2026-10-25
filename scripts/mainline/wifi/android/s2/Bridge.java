package a9.wifi.s2;

import android.content.Context;
import android.net.*;
import android.os.*;
import android.system.Os;
import android.system.OsConstants;
import android.system.StructStat;
import java.io.*;
import java.lang.reflect.Method;
import java.net.*;
import java.nio.file.*;
import java.util.Arrays;
import java.util.concurrent.*;

/** app_process entry. No child command, secret argv/env, exception text, or raw service output. */
public final class Bridge {
    private static final String INPUT="/mnt/a9-wifi/trial/android-s2/input";
    private static final int MAX_REPLY=262144;
    private static final ExecutorService IO=Executors.newSingleThreadExecutor();
    private static IBinder wifi;
    private static Method shell;
    private static String phase="entry";
    private static boolean cleaning=false;
    private static void active() throws Exception {
        if(cleaning) return;
        Method get=Class.forName("android.os.SystemProperties").getMethod("get",String.class);
        if(!"serving".equals(get.invoke(null,"vendor.a9ml.wifi.session.state"))
            || "1".equals(get.invoke(null,"vendor.a9ml.wifi.session.stop"))
            || !"stopped".equals(get.invoke(null,"init.svc.logd"))) throw new IllegalStateException();
    }
    private static void setPhase(String value) {phase=value;note("phase="+value);}
    private static void note(String s) { System.out.println("S2_BRIDGE "+s); }
    private static void init() throws Exception {
        if(android.os.Process.myUid()!=0) throw new IllegalStateException();
        setPhase("runtime-hashes");
        String[][] pins={
            {"/system/framework/framework.jar","9a3161e1879aadf8b9f8ceef162c68d0f0f6ce1ce4825c7161a3b5a19392eb94"},
            {"/apex/com.android.wifi/javalib/framework-wifi.jar","b543262808907844bf72b992e540479853fa87696edc0894be0dfdae45ecd716"},
            {"/apex/com.android.wifi/javalib/service-wifi.jar","401d023dc665492470ffb2cd89d0f96578bda150f1a89ddfaf244210db1759ea"}
        };
        for(String[] pin:pins) {
            java.security.MessageDigest sha=java.security.MessageDigest.getInstance("SHA-256");
            try(InputStream in=Files.newInputStream(Path.of(pin[0]))) {
                byte[] buf=new byte[16384];int n;while((n=in.read(buf))!=-1)sha.update(buf,0,n);
            }
            StringBuilder actual=new StringBuilder();
            for(byte b:sha.digest())actual.append(String.format(java.util.Locale.ROOT,"%02x",b));
            if(!actual.toString().equals(pin[1]))throw new IllegalStateException();
        }
        setPhase("binder-api");
        wifi=(IBinder)Class.forName("android.os.ServiceManager").getMethod("getService",String.class).invoke(null,"wifi");
        if(wifi==null || !wifi.isBinderAlive()) throw new IllegalStateException();
        shell=IBinder.class.getMethod("shellCommand",FileDescriptor.class,FileDescriptor.class,
                FileDescriptor.class,String[].class,Class.forName("android.os.ShellCallback"),ResultReceiver.class);
    }
    private static byte[] bounded(InputStream in,int max) throws Exception {
        ByteArrayOutputStream out=new ByteArrayOutputStream(); byte[] b=new byte[4096]; int n;
        while((n=in.read(b))!=-1) { if(out.size()+n>max) throw new IOException(); out.write(b,0,n); }
        Arrays.fill(b,(byte)0); return out.toByteArray();
    }
    private static String command(String... args) throws Exception {
        active();
        // First argument is a fixed operation name, never an identity/credential.
        String operation=args[0];
        // stdout/stderr are anonymous pipes. Binder parameters are NOT an exec argument vector.
        ParcelFileDescriptor[] pipe=ParcelFileDescriptor.createPipe();
        try(FileInputStream nil=new FileInputStream("/dev/null")) {
            Future<byte[]> read=IO.submit(()-> {try(InputStream in=new ParcelFileDescriptor.AutoCloseInputStream(pipe[0])) {
                return bounded(in,MAX_REPLY);
            }});
            CountDownLatch done=new CountDownLatch(1); int[] rc={-999};
            ResultReceiver result=new ResultReceiver(null) {
                @Override protected void onReceiveResult(int n,Bundle b) {rc[0]=n;done.countDown();}
            };
            try { shell.invoke(wifi,nil.getFD(),pipe[1].getFileDescriptor(),pipe[1].getFileDescriptor(),args,null,result); }
            finally {pipe[1].close();}
            if(!done.await(10,TimeUnit.SECONDS)) {
                note("command="+operation+" result=failed branch=callback-timeout");
                throw new IllegalStateException();
            }
            byte[] bytes=read.get(10,TimeUnit.SECONDS);
            String value=new String(bytes,java.nio.charset.StandardCharsets.UTF_8);
            Arrays.fill(bytes,(byte)0);
            if(rc[0]!=0) {
                String branch=value.contains("SecurityException") || value.contains("Permission Denial")?"security"
                        :value.contains("IllegalArgumentException")?"argument":"service-return";
                note("command="+operation+" result=failed branch="+branch+" rc="+rc[0]);
                throw new IllegalStateException();
            }
            note("command="+operation+" result=accepted rc=0");
            return value;
        } catch(Exception error) {
            Throwable cause=error;
            while(cause instanceof java.lang.reflect.InvocationTargetException && cause.getCause()!=null)cause=cause.getCause();
            String branch=cause instanceof SecurityException?"security":cause instanceof TimeoutException?"reply-timeout"
                    :cause instanceof IOException?"reply-io":"exception";
            note("command="+operation+" result=failed branch="+branch);
            throw error;
        } finally {pipe[0].close();pipe[1].close();}
    }
    private static void publicScan() throws Exception {
        setPhase("scan-identity");
        PublicScan.run(new PublicScan.Identity() {
            public int uid() {return Os.getuid();}
            public int euid() {return Os.geteuid();}
            public int gid() {return Os.getgid();}
            public int egid() {return Os.getegid();}
            public void setgid(int value) throws Exception {Os.setgid(value);}
            public void setuid(int value) throws Exception {Os.setuid(value);}
        },args->{
            note("scan_caller=shell uid=2000 verified=yes");
            setPhase("scan-start");return command(args);
        });
        // Shell command rc only acknowledges dispatch, not completed scanning.
        note("scan=dispatched");
        setPhase("scan-public-results");Thread.sleep(8000);
        String scan=command("list-scan-results");
        if(!scan.startsWith("    BSSID ") && !scan.trim().equals("No scan results"))throw new IllegalStateException();
        note("scan_results="+(scan.trim().equals("No scan results")?"empty":"available"));
    }
    private static Protocol.Input readInput() throws Exception {
        StructStat st=Os.lstat(INPUT);
        if(!OsConstants.S_ISREG(st.st_mode) || st.st_uid!=0 || (st.st_mode&0777)!=0600
                || st.st_nlink!=1 || st.st_size>Protocol.MAX_INPUT) throw Protocol.bad();
        byte[] raw;
        try(InputStream in=Files.newInputStream(Path.of(INPUT),LinkOption.NOFOLLOW_LINKS)) {
            raw=bounded(in,Protocol.MAX_INPUT);
        }
        // One use. Unlink BEFORE the Binder call even when parsing fails.
        Files.delete(Path.of(INPUT));
        try {return Protocol.input(raw);} finally {Arrays.fill(raw,(byte)0);}
    }
    private static ConnectivityManager connectivity() throws Exception {
        if(Looper.getMainLooper()==null) Looper.prepareMainLooper();
        Class<?> c=Class.forName("android.app.ActivityThread");
        Object thread=c.getMethod("systemMain").invoke(null);
        Context context=(Context)c.getMethod("getSystemContext").invoke(thread);
        ConnectivityManager cm=context.getSystemService(ConnectivityManager.class);
        if(cm==null) throw new IllegalStateException(); return cm;
    }
    private static Network dhcp(ConnectivityManager cm) throws Exception {
        String branch="no-default-network";
        for(int i=0;i<60;i++) {
            active();
            Network net=cm.getActiveNetwork();
            if(net!=null) {
                NetworkCapabilities cap=cm.getNetworkCapabilities(net);
                LinkProperties lp=cm.getLinkProperties(net);
                branch="not-wifi-default";
                if(cap!=null && cap.hasTransport(NetworkCapabilities.TRANSPORT_WIFI)
                        && !cap.hasTransport(NetworkCapabilities.TRANSPORT_VPN)
                        && cap.hasCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                        && lp!=null && "wlan0".equals(lp.getInterfaceName())) {
                    boolean address=lp.getLinkAddresses().stream().anyMatch(a->a.getAddress() instanceof Inet4Address
                            && !a.getAddress().isAnyLocalAddress() && !a.getAddress().isLoopbackAddress()
                            && !a.getAddress().isLinkLocalAddress());
                    boolean route=lp.getRoutes().stream().anyMatch(r->r.isDefaultRoute()
                            && r.getGateway() instanceof Inet4Address && !r.getGateway().isAnyLocalAddress());
                    branch=!address?"address-missing":!route?"route-missing":lp.getDnsServers().isEmpty()?"dns-missing"
                            :lp.getDhcpServerAddress()==null?"dhcp-server-missing":"accepted";
                    if(branch.equals("accepted")) {note("dhcp=accepted address=yes route=yes dns=yes server=yes");return net;}
                }
            }
            Thread.sleep(1000);
        }
        note("dhcp=failed branch="+branch); throw new IllegalStateException();
    }
    private static void deviceMac() throws Exception {
        byte[] dt=Files.readAllBytes(Path.of("/sys/class/net/wlan0/device/of_node/local-mac-address"));
        if(dt.length!=6 || (dt[0]&1)!=0) throw new IllegalStateException();
        StringBuilder expected=new StringBuilder();
        for(byte b:dt)expected.append(String.format(java.util.Locale.ROOT,"%02x",b));
        String actual=Files.readString(Path.of("/sys/class/net/wlan0/address")).trim().replace(":","");
        Arrays.fill(dt,(byte)0);
        if(!expected.toString().equals(actual) || actual.equals("000000000000"))throw new IllegalStateException();
    }
    private static void prepareWifi() throws Exception {
        // A credential-free probe must not enable a saved-network auto-connect.
        setPhase("baseline");
        if(Protocol.networks(command("list-networks")).length!=0) throw new IllegalStateException();
        if(command("status").contains("\nWifi is connected to ")) throw new IllegalStateException();
        setPhase("enable");
        Protocol.enableWifi(Bridge::command,()->Thread.sleep(500));
        // Enable and its readback MUST precede any consumption of the one-time input.
        if(!command("status").startsWith("Wifi is enabled\n"))throw new IllegalStateException();
        note("wifi=enabled verified=yes");
        if(Protocol.networks(command("list-networks")).length!=0) throw new IllegalStateException();
        if(command("status").contains("\nWifi is connected to ")) throw new IllegalStateException();
    }
    private static void connect() throws Exception {
        prepareWifi();
        setPhase("input"); Protocol.Input in=readInput();
        setPhase("scan-wait"); Thread.sleep(8000);
        setPhase("scan-results");
        String scan=command("list-scan-results");
        setPhase("scan-select");String target=Protocol.select(scan,in,Bridge::note);scan=null;
        setPhase("connect-dispatch");
        String[] args=Protocol.connectArgs(in,target);
        try {command(args);} finally {Arrays.fill(args,null);in=null;}
        note("dispatch=accepted"); // shell rc is NOT association success: this build returns before callback.
        setPhase("association");
        String branch="not-connected";
        for(int i=0;i<60;i++) {
            branch=Protocol.linkBranch(command("status"),target);
            if(branch.equals("accepted")) {setPhase("device-mac");deviceMac();note("link=COMPLETED security=WPA2 frequency=2427 device_mac=matched");return;}
            Thread.sleep(1000);
        }
        note("link=failed branch="+branch);
        throw new IllegalStateException();
    }
    private static void web() throws Exception {
        setPhase("connectivity-api"); ConnectivityManager cm=connectivity();
        setPhase("dhcp");Network net=dhcp(cm);
        setPhase("clock");
        long now=System.currentTimeMillis();
        if(!Protocol.clockSane(now)) throw new IllegalStateException(); // 2026-10-01..2028-10-17; TLS checks certificate validity.
        setPhase("https");active();
        // Network.openConnection binds routing AND DNS to this framework Wi-Fi network.
        HttpURLConnection conn=(HttpURLConnection)net.openConnection(new URL("https://example.com/"));
        conn.setConnectTimeout(10000);conn.setReadTimeout(10000);conn.setInstanceFollowRedirects(false);
        conn.setUseCaches(false);
        conn.setRequestProperty("User-Agent","A9-S2/1");
        try {
            int status=conn.getResponseCode();
            if(status!=200) {note("web=failed branch=http-status status="+status);throw new IllegalStateException();}
            byte[] page;
            try(InputStream body=conn.getInputStream()) {page=bounded(body,65536);}
            boolean marker=new String(page,java.nio.charset.StandardCharsets.UTF_8).contains("Example Domain");
            Arrays.fill(page,(byte)0);
            if(!marker) throw new IllegalStateException();
            note("web=accepted transport=wifi tls=verified status=200 marker=yes");
            setPhase("browser");active();
            try {
                Class<?> c=Class.forName("android.app.ActivityThread");
                Object thread=c.getMethod("currentActivityThread").invoke(null);
                Context context=(Context)c.getMethod("getSystemContext").invoke(thread);
                android.content.Intent intent=new android.content.Intent(android.content.Intent.ACTION_VIEW,
                        Uri.parse("https://example.com/"));
                intent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
                context.startActivity(intent);
                note("browser=requested camera=unverified");
                Thread.sleep(15000);
            } catch(Exception ignored) {note("browser=unavailable camera=unverified");}
        } finally {conn.disconnect();}
    }
    private static void cleanup() throws Exception {
        cleaning=true;
        setPhase("disable");Protocol.disableWifi(Bridge::command,()->Thread.sleep(500));
        setPhase("forget");
        // Entry requires an empty saved-network list and a fresh RAM /data. Every resulting
        // config belongs to this single S2 attempt; erase only through the framework.
        int[] ids=Protocol.networks(command("list-networks"));
        if(ids.length>1) throw new IllegalStateException();
        for(int id:ids)command("forget-network",Integer.toString(id));
        for(int i=0;i<10;i++) {
            if(Protocol.networks(command("list-networks")).length==0) {note("cleanup=forgotten");return;}
            Thread.sleep(500);
        }
        throw new IllegalStateException();
    }
    public static void main(String[] args) {
        int rc=1;
        // Suppress all incidental stack traces, including Android runtime failures after init.
        Thread.setDefaultUncaughtExceptionHandler((t,e)->{note("result=failed phase=runtime");System.exit(1);});
        try {
            if(args.length!=1) throw new IllegalArgumentException();
            setPhase("binder-api");init();
            switch(args[0]) {
                case "probe": prepareWifi();setPhase("probe-connectivity");connectivity();note("probe=accepted");break;
                case "scan": publicScan();break;
                case "connect": connect();break;
                case "web": web();break;
                case "cleanup": cleanup();break;
                default:throw new IllegalArgumentException();
            }
            rc=0;
        } catch(Throwable ignored) {note("result=failed phase="+phase);}
        finally {IO.shutdownNow();System.exit(rc);}
    }
}
