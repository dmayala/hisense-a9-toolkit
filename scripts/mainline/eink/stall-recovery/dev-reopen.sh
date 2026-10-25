#!/system/bin/sh
# Run from the user's admitted root console. Only typed starvation recovery;
# never acknowledges electrical faults or writes either clear_fault.
exec sh /vendor/bin/a9ml-eink.sh recover
