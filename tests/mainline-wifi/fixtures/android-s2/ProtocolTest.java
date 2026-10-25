package a9.wifi.s2;
import java.nio.charset.StandardCharsets;
import java.util.Base64;
import java.util.Locale;

public class ProtocolTest {
    private static int cases;
    private static void check(boolean x) {cases++;if(!x)throw new AssertionError("fixture-rejected");}
    private static byte[] frame(String ssid,String pass) {
        StringBuilder hex=new StringBuilder();for(byte b:ssid.getBytes(StandardCharsets.UTF_8))hex.append(String.format("%02x",b));
        return ("version=1\ncountry=US\nfrequency=2427\nssid_hex="+hex+"\npassphrase_b64="
            +Base64.getEncoder().encodeToString(pass.getBytes(StandardCharsets.UTF_8))+"\n").getBytes(StandardCharsets.US_ASCII);
    }
    private static String row(String name,String bssid,int freq,String flags,String age) {
        return String.format(Locale.US,"  %17s  %9d  %18s   %7s    %-32s  %s\n",bssid,freq,"      -45         ",age,name,flags);
    }
    private static void reject(byte[] b) {boolean bad=false;try{Protocol.input(b);}catch(Exception e){bad=true;}check(bad);}
    private static void noTarget(String scan,Protocol.Input in) {boolean bad=false;try{Protocol.select(scan,in);}catch(Exception e){bad=true;}check(bad);}
    public static void main(String[] args) {
        try {
            java.util.List<String> calls=new java.util.ArrayList<>();
            int[] polls={0};
            Protocol.enableWifi(a->{calls.add(a[0]);return a[0].equals("status") && ++polls[0]>2?"Wifi is enabled\n":"Wifi is disabled\n";},()->{});
            check(calls.get(0).equals("set-wifi-enabled"));check(polls[0]==3);
            check(!calls.contains("input"));
            boolean failed=false;
            try{Protocol.enableWifi(a->"Wifi is disabled\n",()->{});}catch(Exception e){failed=true;}check(failed);
            polls[0]=0;
            Protocol.disableWifi(a->a[0].equals("status") && ++polls[0]>2?"Wifi is disabled\n":"Wifi is enabled\n",()->{});
            check(polls[0]==3);failed=false;
            try{Protocol.disableWifi(a->"Wifi is enabled\n",()->{});}catch(Exception e){failed=true;}check(failed);
            check(Protocol.clockSane(1792253040000L)); // measured 2026-10-17 phone clock
            check(Protocol.clockSane(1790812800000L));check(!Protocol.clockSane(0));
            check(!Protocol.clockSane(1790812799999L));check(!Protocol.clockSane(1855353600001L));
            String name="fixture network",pass="fixturepass!",addr="00:00:5e:00:53:02",flags="[WPA2-PSK-CCMP][ESS]";
            Protocol.Input in=Protocol.input(frame(name,pass));check(in.ssid.equals(name));check(in.passphrase.equals(pass));
            String scan=row(name,addr,2427,flags,"0.500");
            check(Protocol.select(scan,in).equals(addr));
            for(String rssi:new String[]{String.format(Locale.US,"%5d(%1d:%3d)       ",-45,0,-45),
                    String.format(Locale.US,"%5d(%1d:%3d/%1d:%3d)",-45,0,-45,1,-48),
                    String.format(Locale.US,"%5d(%1d:%3d)       ",-100,0,-100),
                    String.format(Locale.US,"%5d(%1d:%3d/%1d:%3d)",-100,0,-100,1,-127)}) {
                String chains=String.format(Locale.US,"  %17s  %9d  %18s   %7s    %-32s  %s\n",addr,2427,rssi,"0.500",name,flags);
                check(Protocol.select(chains,in).equals(addr));
            }
            check(Protocol.connectArgs(in,addr)[1].equals(in.hex));check(Protocol.connectArgs(in,addr)[3].equals(pass));
            check(String.join(" ",Protocol.connectArgs(in,addr)).endsWith("-r none"));
            noTarget(scan+scan,in);noTarget(row(name,addr,2432,flags,"0.500"),in);
            noTarget(row("other",addr,2427,flags,"0.500"),in);noTarget(row(name,addr,2427,"[ESS]","0.500"),in);
            noTarget(row(name,addr,2427,flags,"31.000"),in);noTarget(row(name,addr,2427,flags,"___?___"),in);
            noTarget(row(name,addr,2427,"[WPA2-PSK-CCMP+TKIP][ESS]","0.500"),in);
            // Locale-specific output from the exact shipped String.format calls.
            // This failed with87j code: the target is fresh, but its age uses a comma.
            Locale saved=Locale.getDefault();
            try {
                Locale.setDefault(Locale.GERMANY);
                String age=String.format("%3.3f",Double.valueOf(0.5));
                check(age.equals("0,500"));
                check(Protocol.select(row(name,addr,2427,flags,age),in).equals(addr));
                noTarget(row(name,addr,2427,flags,"30,001"),in);
                noTarget(row(name,addr,2427,flags,"1,000.0"),in);
                noTarget(row(name,addr,2427,flags,"NaN"),in);
            } finally {Locale.setDefault(saved);}
            java.util.List<String> notes=new java.util.ArrayList<>();
            String[] samples={"No scan results\n","  invalid private-row-marker\n",
                row("other",addr,2427,flags,"0.500"),row(name,addr,2432,flags,"0.500"),
                row(name,addr,2427,"[ESS]","0.500"),row(name,addr,2427,flags,"31.000"),scan+scan,scan};
            String[] branches={"no-rows","row-format","ssid-absent","frequency-mismatch","security-mismatch","age-rejected","ambiguous","accepted"};
            for(int i=0;i<samples.length;i++) {
                notes.clear();boolean rejected=false;
                try {Protocol.select(samples[i],in,notes::add);}catch(IllegalStateException e){rejected=true;}
                check(rejected==(i!=samples.length-1));check(notes.size()==1);
                String diag=notes.get(0);check(diag.startsWith("select="+branches[i]+" rows="));
                check(!diag.contains(name) && !diag.contains(pass) && !diag.contains(addr) && !diag.contains("private-row-marker"));
            }
            String status="Wifi is enabled\nWifi is connected to fixture network\nWifiInfo: SSID: fixture network, BSSID: "+addr
                +", MAC: 00:00:5e:00:53:03, IP: /192.0.2.3, Security type: 2, Supplicant state: COMPLETED, Wi-Fi standard: 4, RSSI: -45, Frequency: 2427MHz, Net ID: 0, score: 60";
            check(Protocol.completed(status,addr));check(!Protocol.completed(status.replace("COMPLETED","SCANNING"),addr));
            check(!Protocol.completed(status.replace("2427","2432"),addr));check(!Protocol.completed(status.replace("type: 2","type: 4"),addr));
            check(!Protocol.completed(status,"02:00:00:00:00:02"));
            check(!Protocol.completed(status.replace("2427MHz","24270MHz"),addr));
            check(!Protocol.completed(status+"\n"+status,addr));
            check(!Protocol.completed(status.replace("type: 2","type: 2, Security type: 2"),addr));
            check(Protocol.networks("No networks\n").length==0);
            check(Protocol.networks("Network Id      SSID                         Security type\n0            fixture network                  wpa2\n")[0]==0);
            for(String bad:new String[]{"","short","bad\\slash","quote\"here","a".repeat(64),"line\nbreak"})reject(frame(name,bad));
            for(String bad:new String[]{"","a".repeat(33)," leading","trailing ","nonascii-ë","a\nb"})reject(frame(bad,pass));
            reject(new byte[513]);reject(new String(frame(name,pass),StandardCharsets.US_ASCII).replace("country=US","country=CN").getBytes());
            reject(new String(frame(name,pass),StandardCharsets.US_ASCII).replace("frequency=2427","frequency=2467").getBytes());
            reject(new String(frame(name,pass),StandardCharsets.US_ASCII).replace("version=1","version=2").getBytes());
            System.out.println("S2_PROTOCOL cases="+cases+" result=pass");
        } catch(Throwable ignored) {System.out.println("S2_PROTOCOL result=fail");System.exit(1);}
    }
}
