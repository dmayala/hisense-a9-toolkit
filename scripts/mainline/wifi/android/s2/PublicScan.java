package a9.wifi.s2;

/** Public-only one-shot process. Never receives or opens the private input. */
public final class PublicScan {
    public interface Identity {
        int uid(); int euid(); int gid(); int egid();
        void setgid(int value) throws Exception;
        void setuid(int value) throws Exception;
    }
    public static void run(Identity identity, Protocol.Command command) throws Exception {
        if(identity.uid()!=0 || identity.euid()!=0) throw new IllegalStateException();
        // Permanent process drop, before the first Binder command or IO thread.
        // The private input and bundle directories remain root-only.
        identity.setgid(2000);
        identity.setuid(2000);
        if(identity.uid()!=2000 || identity.euid()!=2000
                || identity.gid()!=2000 || identity.egid()!=2000) throw new IllegalStateException();
        command.call("start-scan");
    }
    private PublicScan() {}
}
