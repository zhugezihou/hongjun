#!/bin/bash
cd /home/asus/hongjun/src
export MINIMAX_API_KEY="sk-cp-UoCoTWJGd4RDlRiTpruNLhiMUq5xt3WP8gq2Mf-Gt8zvh0pusXNGtWcU8ieizbXzlpEb8Q6nzDtjO1Na7WXEhrRQOBb7y3UpEN83BzP-0HSSO2gT8nitvrA"
PYTHONPATH=/home/asus/hongjun/src PYTHONUNBUFFERED=1 exec python3 -m hongjun.gateway --port 20831 >> /home/asus/hongjun/gateway_stderr.log 2>&1
