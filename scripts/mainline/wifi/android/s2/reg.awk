# Derived from wifi-assoc dfde942 regulatory parser. p is a numeric wiphy index.
function code(v) {return v~/^([A-Z][A-Z]|00|98|99):$/ ? substr(v,1,2) : "INVALID"}
      BEGIN {gc="NONE";pc="NONE";mode="inherited"}
      /^global$/ {scope="global";g++;next}
      /^phy#/ {
        scope="other"
        if($1=="phy#" p) {
          t++;scope="phy"
          if(NF==1)mode="normal"
          else if(NF==2 && $2=="(self-managed)")mode="self-managed"
          else {mode="unknown";bad=1}
        }
        next
      }
      /^country / {
        if(scope=="global") {n++;gc=code($2)}
        if(scope=="phy") {m++;pc=code($2)}
        if(scope=="global" || scope=="phy") {
          if(NF!=3 || $3!~/^DFS-(UNSET|FCC|ETSI|JP)$/)bad=1
          scope="rules"
        }
      }
      END {
        ok=(g==1 && n==1 && gc!="INVALID" && t<=1 && ((t==0 && m==0) || (t==1 && m==1 && pc!="INVALID")) && !bad)
        if(!(ok && gc=="US" && (mode=="normal" || mode=="inherited"))) bad_result=1
        print "global=" gc " phy=" pc " mode=" mode " syntax=" (ok ? "ok" : "bad")
        exit bad_result
      }
