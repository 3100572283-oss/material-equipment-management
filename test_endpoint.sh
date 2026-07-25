#!/bin/bash
COOKIE=/tmp/cookies.txt
rm -f $COOKIE
curl -s -c $COOKIE -b $COOKIE -L "http://127.0.0.1:80/auth/login" -d "username=test_user_view&password=Test@123456" -o /dev/null
echo "=== test_user_view login ==="
for path in /stock_in/ /stock_out/ /contract/ /equipment/; do
    HTTP=$(curl -s -b $COOKIE -o /dev/null -w "%{http_code}" "http://127.0.0.1:80$path")
    echo "  $path => $HTTP"
done
rm -f $COOKIE
