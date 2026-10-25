package a9.wifi.s2;

import java.nio.charset.StandardCharsets;
import java.nio.charset.CodingErrorAction;
import java.nio.ByteBuffer;
import java.util.Base64;
import java.util.regex.*;

/** Pure parsing core. Errors are fixed labels; never attach an input or exception. */
public final class Protocol {
    public static final int MAX_INPUT = 512;
    public static final class Input {
        public final String ssid, hex, passphrase;
        Input(String s, String h, String p) { ssid=s; hex=h; passphrase=p; }
    }
    public static IllegalArgumentException bad() { return new IllegalArgumentException("input-rejected"); }
    public static Input input(byte[] bytes) throws Exception {
        if (bytes.length>MAX_INPUT) throw bad();
        String[] lines=new String(bytes, StandardCharsets.US_ASCII).split("\n", -1);
        if (lines.length!=6 || !lines[0].equals("version=1") || !lines[1].equals("country=US")
                || !lines[2].equals("frequency=2427") || !lines[3].startsWith("ssid_hex=")
                || !lines[4].startsWith("passphrase_b64=") || !lines[5].isEmpty()) throw bad();
        String hex=lines[3].substring(9);
        if (!hex.matches("(?:[0-9a-f]{2}){1,32}")) throw bad();
        byte[] raw=new byte[hex.length()/2];
        for(int i=0;i<raw.length;i++) raw[i]=(byte)Integer.parseInt(hex.substring(2*i,2*i+2),16);
        String ssid=StandardCharsets.UTF_8.newDecoder().onMalformedInput(CodingErrorAction.REPORT)
                .decode(ByteBuffer.wrap(raw)).toString();
        // Scope of the shipped fixed-width scan renderer. No lossy/truncated matching.
        if (!ssid.matches("[\\x20-\\x7e]{1,32}") || !ssid.equals(ssid.trim())) throw bad();
        String encoded=lines[4].substring(15);
        byte[] pass=Base64.getDecoder().decode(encoded);
        if (!Base64.getEncoder().encodeToString(pass).equals(encoded)) throw bad();
        String p=new String(pass,StandardCharsets.US_ASCII);
        if (!p.matches("[\\x20-\\x7e]{8,63}") || p.indexOf('"')>=0 || p.indexOf('\\')>=0) throw bad();
        java.util.Arrays.fill(raw,(byte)0); java.util.Arrays.fill(pass,(byte)0);
        return new Input(ssid,hex,p);
    }
    // Shipped printf fields have minimum widths: chain values below -99 widen the RSSI column.
    private static final Pattern ROW=Pattern.compile("^  ([0-9a-fA-F:]{17})  (.{9})  ( *-?[0-9]+ *(?:\\([0-9]+: *-?[0-9]+(?:/[0-9]+: *-?[0-9]+)?\\) *)?)   (.{7})    (.{32})  (.*)$");
    public static String select(String scan, Input in) {
        return select(scan,in,value->{});
    }
    // Diagnostics contain fixed enums and aggregate counts only. Never emit a row,
    // SSID, BSSID, passphrase, exception message, or a hash of those values.
    public static String select(String scan, Input in, java.util.function.Consumer<String> note) {
        String selected=null; int rows=0,parsed=0,named=0,frequency=0,security=0,fresh=0;
        for(String line:scan.split("\n")) {
            if(line.startsWith("  ") && !line.startsWith("    BSSID "))rows++;
            Matcher m=ROW.matcher(line);
            if(!m.matches() || !m.group(1).matches("(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}")) continue;
            parsed++;
            if (!m.group(5).stripTrailing().equals(in.ssid)) continue;
            named++;
            if (!m.group(2).trim().equals("2427")) continue;
            frequency++;
            String flags=m.group(6);
            if (!flags.contains("[WPA2-PSK-CCMP]") || flags.contains("TKIP") || flags.contains("WEP")) continue;
            security++;
            String age=m.group(4).trim();
            // ScanResultUtil uses String.format without Locale.ROOT. Accept its
            // dot/comma decimal separator with the SAME strict 30-second limit.
            if (!age.matches("[0-9]+[.,][0-9]{3}") || Double.parseDouble(age.replace(',','.'))>30.0) continue;
            fresh++; selected=m.group(1).toLowerCase(java.util.Locale.ROOT);
        }
        String branch=fresh==1?"accepted":fresh>1?"ambiguous":rows==0?"no-rows":parsed==0?"row-format"
                :named==0?"ssid-absent":frequency==0?"frequency-mismatch":security==0?"security-mismatch":"age-rejected";
        note.accept("select="+branch+" rows="+rows+" parsed="+parsed+" named="+named+" frequency="+frequency
                +" security="+security+" fresh="+fresh);
        if(fresh!=1) throw new IllegalStateException("scan-target-missing-or-ambiguous");
        return selected;
    }
    public static String[] connectArgs(Input in, String bssid) {
        return new String[]{"connect-network",in.hex,"wpa2",in.passphrase,"-x","-b",bssid,"-r","none"};
    }
    private static String field(String tail, String name) {
        String key=", "+name+": ";int first=tail.indexOf(key);
        if(first<0 || tail.indexOf(key,first+key.length())>=0)return "";
        int end=tail.indexOf(", ",first+key.length());
        return end<0?"":tail.substring(first+key.length(),end);
    }
    public static String linkBranch(String status, String bssid) {
        if(!status.startsWith("Wifi is enabled\n"))return "wifi-disabled";
        if(!status.contains("\nWifi is connected to "))return "not-connected";
        String info=null;int n=0;
        for(String line:status.split("\n"))if(line.startsWith("WifiInfo: ")){info=line;n++;}
        if(n!=1)return "info-missing-or-duplicate";
        String marker=", BSSID: "+bssid+", MAC: ";int pos=info.indexOf(marker);
        if(pos<0)return "target-mismatch";
        String tail=info.substring(pos+marker.length());
        if(!field(tail,"Supplicant state").equals("COMPLETED"))return "state-incomplete";
        if(!field(tail,"Frequency").equals("2427MHz"))return "frequency-mismatch";
        if(!field(tail,"Security type").equals("2"))return "security-mismatch";
        return "accepted";
    }
    public static boolean completed(String status,String bssid) {return linkBranch(status,bssid).equals("accepted");}
    @FunctionalInterface public interface Command {String call(String... args) throws Exception;}
    @FunctionalInterface public interface Pause {void call() throws Exception;}
    public static void enableWifi(Command command,Pause pause) throws Exception {
        setWifi(command,pause,"enabled");
    }
    public static void disableWifi(Command command,Pause pause) throws Exception {
        setWifi(command,pause,"disabled");
    }
    private static void setWifi(Command command,Pause pause,String state) throws Exception {
        command.call("set-wifi-enabled",state);
        for(int i=0;i<20;i++) {
            if(command.call("status").startsWith("Wifi is "+state+"\n"))return;
            pause.call();
        }
        throw new IllegalStateException("wifi-state-timeout");
    }
    public static boolean clockSane(long now) {return now>=1790812800000L && now<=1855353600000L;}
    public static int[] networks(String text) {
        if(text.trim().equals("No networks")) return new int[0];
        String[] rows=text.strip().split("\n");
        if(rows.length<2 || !rows[0].startsWith("Network Id      SSID")) throw new IllegalStateException("network-list-format");
        int[] ids=new int[rows.length-1];
        for(int i=1;i<rows.length;i++) {
            String n=rows[i].trim().split("\\s+",2)[0];
            if(!n.matches("[0-9]{1,9}")) throw new IllegalStateException("network-list-format");
            ids[i-1]=Integer.parseInt(n);
        }
        return ids;
    }
}
