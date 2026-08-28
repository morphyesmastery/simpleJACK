#!/data/data/com.termux/files/usr/bin/bash
# Test script - logs everything
LOG=/data/data/com.termux/files/home/ping-test.log
echo "=== Test run $(date) ===" > $LOG
echo "PATH=$PATH" >> $LOG
echo "which ping: $(which ping)" >> $LOG
echo "--- direct ping ---" >> $LOG
/data/data/com.termux/files/usr/bin/ping -c 3 100.76.112.128 >> $LOG 2>&1
echo "exit=$?" >> $LOG
echo "--- done ---" >> $LOG
cat $LOG
