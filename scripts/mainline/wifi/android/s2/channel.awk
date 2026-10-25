# Authorised primary frequency, one enabled IR row with numeric ceiling <=20 dBm.
$1=="*" && $2==2427 && $3=="MHz" {
 n++
 if($0~/disabled|no IR|no-IR|radar|DFS/)bad=1
 if($5!~/^\([0-9]+([.][0-9]+)?$/ || length($5)>24 || $6!="dBm)" || (substr($5,2)+0)>20)bad=1
 else power=substr($5,2)
}
END {
 ok=(n==1 && !bad)
 print "channel=" (ok?"accepted":"rejected") " frequency=2427 max_dbm=" (ok?power:"none")
 exit !ok
}
