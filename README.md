curl "https://api.polymarket.us/v1/markets" \
-H "POLY-ADDRESS: d5f9f2cf-7fa3-4841-a343-a21956f53f21" \
-H "POLY-SIGNATURE: BQNfGILRU4cJDrOFwkoCtOuApBQpVCbh6KcY8oWjb PyN0269ZejV5kxsKjnzj0F3FTHVwgvJpzUBX/LNKmJCA==" \
-H "POLY-TIMESTAMP: $(date +%s)" | head -50