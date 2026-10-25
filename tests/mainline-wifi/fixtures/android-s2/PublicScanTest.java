package a9.wifi.s2;

import java.util.ArrayList;
import java.util.List;

/** Controlled replay of UID transitions; does not invoke native identity calls or Binder. */
public final class PublicScanTest {
    private static int cases;
    private static void check(boolean condition) {
        cases++;if(!condition)throw new AssertionError("public-scan-fixture");
    }
    private static final class Caller implements PublicScan.Identity {
        int uid=0,euid=0,gid=0,egid=0;
        String fault="";
        List<String> events=new ArrayList<>();
        public int uid() {return uid;}
        public int euid() {return euid;}
        public int gid() {return gid;}
        public int egid() {return egid;}
        public void setgid(int value) throws Exception {
            events.add("gid");
            if(fault.equals("gid-denied"))throw new SecurityException();
            if(!fault.equals("gid-unchanged"))gid=value;
            if(!fault.equals("egid-unchanged"))egid=value;
        }
        public void setuid(int value) throws Exception {
            events.add("uid");
            if(fault.equals("uid-denied"))throw new SecurityException();
            if(!fault.equals("uid-unchanged"))uid=value;
            if(!fault.equals("euid-unchanged"))euid=value;
        }
    }
    public static void main(String[] args) throws Exception {
        Caller caller=new Caller();
        PublicScan.run(caller,argv->{
            check(caller.uid==2000 && caller.euid==2000 && caller.gid==2000 && caller.egid==2000);
            check(argv.length==1 && argv[0].equals("start-scan"));
            caller.events.add("binder");return "";
        });
        check(caller.events.equals(List.of("gid","uid","binder")));
        check(caller.uid==2000 && caller.euid==2000); // never restores this process to root
        for(String fault:List.of("gid-denied","uid-denied","gid-unchanged","egid-unchanged","uid-unchanged","euid-unchanged",
                "entry-uid","entry-euid")) {
            Caller bad=new Caller();bad.fault=fault;
            if(fault.equals("entry-uid"))bad.uid=2000;
            if(fault.equals("entry-euid"))bad.euid=2000;
            boolean rejected=false;
            try {PublicScan.run(bad,argv->{bad.events.add("binder");return "";});}
            catch(Exception error) {rejected=true;}
            check(rejected);check(!bad.events.contains("binder"));
        }
        Caller denied=new Caller();boolean rejected=false;
        try {PublicScan.run(denied,argv->{denied.events.add("binder");throw new SecurityException();});}
        catch(SecurityException error) {rejected=true;}
        check(rejected);check(denied.events.equals(List.of("gid","uid","binder")));
        check(denied.uid==2000); // no fallback/retry as root on a denied dispatch
        System.out.println("S2_PUBLIC_SCAN cases="+cases+" result=pass");
    }
}
