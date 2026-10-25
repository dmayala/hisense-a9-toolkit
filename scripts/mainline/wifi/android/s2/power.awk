# Same trial-22 acceptance and fixed diagnostics; stage=before|after, rc=iw exit code.
BEGIN {value="none";line="none";branch="missing"}
      index($0,"txpower") && line=="none" {line="redacted"}
      $1=="txpower" {
        n++
        if(n==1) {
          if($3=="dBm" && $2~/^-?[0-9]+([.][0-9]+)?$/ && length($2)<=24)value=$2
          if(NF==3 && $3=="dBm" && value!="none")line="txpower " value " dBm"
          if(NF!=3)branch="fields"
          else if($3!="dBm")branch="unit"
          else if($2~/^-[0-9]+([.][0-9]+)?$/)branch="negative"
          else if($2!~/^[0-9]+([.][0-9]+)?$/)branch="number"
          else if($2>20)branch="over-cap"
          else branch="accepted"
        }
      }
      END {
        if(n==0 && stage=="before")branch="unreported"
        if(n>1){branch="duplicate";value="none"}
        if(rc!=0)branch="command-failed"
        print "rc=" rc " branch=" branch " dbm=" value
        print "line=" line
        exit !(rc==0 && (branch=="accepted" || (stage=="before" && branch=="unreported")))
      }
