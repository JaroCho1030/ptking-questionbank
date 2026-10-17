# 常用指令。第一次：python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt
.PHONY: fetch parse validate tag build test all
fetch:     ; python -m pipeline.fetch --years 105-115
parse:     ; python -m pipeline.parse
validate:  ; python -m pipeline.validate
tag:       ; python -m pipeline.tag
build:     ; python -m pipeline.build
test:      ; python -m pytest -q
all: fetch parse validate
