# ChemQ
# 前置依赖只有两个：Docker Desktop、uv。
# psql 走容器（不需要本机装 PostgreSQL 客户端），Python 走 uv（不需要动系统 Python）。

SHELL := /bin/bash

DSN        ?= postgresql://postgres:chemq@localhost:5432/chemq
DSN_RO     ?= postgresql://chemq_ro_login:chemq@localhost:5432/chemq
PSQL       ?= docker compose exec -T db psql
# SQL 文件的路径。默认是容器内的挂载点（db/ 挂到了这里）。
# 如果你把 PSQL 改成本机 psql，把 SQLDIR 一起改成 db
SQLDIR     ?= /docker-entrypoint-initdb.d
PY         ?= uv run --quiet --no-project --with pyyaml python

.PHONY: help doctor doctor-w1 up down logs seed golden catalog reset psql sql \
        install install-db install-rag install-agent install-all mcp agent agent-c ingest ingest-dry test eval eval-dry eval-rag eval-kb critic-lab runs-kb agg agg-vs runs lint gateway clean

help:            ## 显示所有命令
	@grep -E '^[a-zA-Z0-9_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN{FS=":.*?## "};{printf "  \033[36m%-11s\033[0m %s\n",$$1,$$2}'

doctor-w1:       ## W1 体检：不需要 Docker / 数据库，能跑就能开始第一周
	@echo "--- W1 只需要这三样 ---"
	@command -v uv >/dev/null && echo "  ✓ uv      $$(uv --version)" \
		|| echo "  ✗ uv        未安装 -> curl -LsSf https://astral.sh/uv/install.sh | sh"
	@test -d ai/.venv && echo "  ✓ ai/.venv 已创建" || echo "  ✗ ai/.venv  未创建 -> make install"
	@test -f .env && grep -q "^CHEMQ_ANTHROPIC_API_KEY=sk-" .env 2>/dev/null \
		&& echo "  ✓ .env    API key 已填" \
		|| echo "  ✗ .env      未填 API key -> cp .env.example .env 然后填 CHEMQ_ANTHROPIC_API_KEY"
	@cd ai && uv run --quiet python -c "import chemq, yaml, anthropic, sqlglot; print('  ✓ 依赖    chemq 包可导入')" \
		2>/dev/null || echo "  ✗ 依赖      装得不全 -> make install"
	@echo ""
	@echo "  以上全绿就可以开始 W1：make test && make eval-dry && make eval WEEK=1"
	@echo "  W1 不碰数据库，Docker 装不上也不影响。"

doctor:          ## 完整体检（含数据库）—— W2 起用这个
	@echo "--- 依赖检查 ---"
	@command -v docker >/dev/null && echo "  ✓ docker  $$(docker --version)" \
		|| echo "  ✗ docker    未安装 -> https://www.docker.com/products/docker-desktop/"
	@docker info >/dev/null 2>&1 && echo "  ✓ docker 守护进程已在运行" \
		|| echo "  ! Docker 不可用 —— W1 不受影响；W2 起需要数据库，见 docs/03-no-docker.md"
	@command -v uv >/dev/null && echo "  ✓ uv      $$(uv --version)" \
		|| echo "  ✗ uv        未安装 -> curl -LsSf https://astral.sh/uv/install.sh | sh"
	@command -v make >/dev/null && echo "  ✓ make    $$(make --version | head -1)" \
		|| echo "  ✗ make      未安装 -> xcode-select --install"
	@test -f .env && echo "  ✓ .env    已创建" \
		|| echo "  ✗ .env      未创建 -> cp .env.example .env 然后填 API key"
	@echo "--- 数据库 ---"
	@$(PSQL) "$(DSN)" -tAc "select '  ✓ 可连接，chem schema 有 '||count(*)||' 张表/视图' \
		from information_schema.tables where table_schema='chem'" 2>/dev/null \
		|| echo "  ✗ 连不上数据库 -> make up"

up:              ## 起数据库（首次会自动建表并导入数据，约 30 秒）
	docker compose up -d --wait
	@echo "✓ 数据库就绪。首次启动已自动执行 db/01→04，可直接 make doctor 验证"

down:            ## 停数据库（数据保留在 volume 里）
	docker compose down

logs:            ## 看数据库日志 —— 起不来时看这里
	docker compose logs -f db

seed:            ## 重建数据 + 重算评测集标准答案（改了 naming.yaml 之后跑）
	$(PY) db/gen_seed.py > db/02_seed.sql
	$(PSQL) "$(DSN)" -v ON_ERROR_STOP=1 -q \
		-f $(SQLDIR)/01_schema.sql \
		-f $(SQLDIR)/02_seed.sql \
		-f $(SQLDIR)/03_semantic_views.sql \
		-f $(SQLDIR)/04_roles.sql
	CHEMQ_PSQL="$(PSQL)" CHEMQ_PG_DSN="$(DSN)" $(PY) ai/evals/build_golden.py
	CHEMQ_PSQL="$(PSQL)" CHEMQ_PG_DSN="$(DSN)" $(PY) db/build_catalog.py
	@echo "✓ 数据、评测集标准答案、语义层目录已重建（知识库被清空了，记得 make ingest）"

golden:          ## 只重算评测集标准答案（golden.jsonl + holdout.jsonl），不动数据库数据
	CHEMQ_PSQL="$(PSQL)" CHEMQ_PG_DSN="$(DSN)" $(PY) ai/evals/build_golden.py

catalog:         ## 只重新生成语义层目录 db/semantic_catalog.md（text2sql 提示用）
	CHEMQ_PSQL="$(PSQL)" CHEMQ_PG_DSN="$(DSN)" $(PY) db/build_catalog.py

reset:           ## 连 volume 一起删掉重来（数据全没，慎用）
	docker compose down -v
	$(MAKE) up

psql:            ## 用只读账号进 psql —— 手写 SQL 验证你的想法
	$(PSQL) "$(DSN_RO)"

sql:             ## 跑一条 SQL  用法: make sql Q="select * from chem.v_monthly_summary"
	@$(PSQL) "$(DSN_RO)" -P pager=off -c "set search_path=chem,public; $$Q"

install:         ## 装最小依赖（W1 够用，不含数据库/Agent SDK）
	cd ai && uv sync --extra dev
	@echo "✓ 解释器在 ai/.venv/bin/python —— VS Code 里 Cmd+Shift+P > Python: Select Interpreter 选它"

install-db:      ## 加装数据库依赖（W2 起）
	cd ai && uv sync --extra dev --extra db

install-rag:     ## 加装 RAG 依赖（W3 起：数据库 + boto3[crt]）
	cd ai && uv sync --extra dev --extra db --extra aws

install-agent:   ## 加装 Agent 依赖（W4 起：数据库 + boto3 + claude-agent-sdk + mcp）
	cd ai && uv sync --extra dev --extra db --extra aws --extra agent

agent:           ## 用 Agent SDK 回答一个问题并打印调用过程  用法: make agent Q="上个月哪个装置单位能耗最高"
	cd ai && uv run python -m chemq.agents "$$Q"

agent-c:         ## 破坏实验 C：不限制自带工具，只靠 hook 拦  用法: make agent-c Q="..."
	cd ai && CHEMQ_AGENT_EXPERIMENT_C=1 uv run python -m chemq.agents "$$Q"

mcp:             ## 前台跑 MCP server（stdio，一般由 Claude Code 拉起，手动跑只用来看能不能启动）
	cd ai && uv run python -m chemq.mcp_server

install-all:     ## 全装（W4 起：Agent SDK / MCP / FastAPI / boto3）
	cd ai && uv sync --all-extras

ingest-dry:      ## 只切块看结果，不调 AWS、不写库
	cd ai && uv run python -m chemq.ingest --dry

ingest:          ## 知识文档写进库（先 aws login --profile chemq；make seed 之后要重跑）
	cd ai && uv run python -m chemq.ingest

test:            ## 跑单元测试（SQL 护栏在这里）
	cd ai && uv run pytest -q

eval-dry:        ## 不调 API，只自检评测集
	cd ai && uv run python evals/run_eval.py --week 6 --dry

eval:            ## 跑评测  用法: make eval WEEK=1  （W4：ENGINE=agent 换做法，SET=holdout 跑新题）
	cd ai && uv run python evals/run_eval.py --week $${WEEK:-1} --engine $${ENGINE:-pipeline} --set $${SET:-golden} --json ../eval-report.json

eval-rag:        ## 检索评测：向量 / BM25 / 混合 各自的 Hit@K、Recall@K（需先 make ingest；默认 8 条，K=5 对比）
	@mkdir -p runs
	cd ai && uv run python evals/run_retrieval.py --k $${K:-8} --json ../runs/retrieval-k$${K:-8}.json

eval-kb:         ## 知识库问答评测：检索 + Claude 回答 + 引用（需 API key；用法 make eval-kb ONLY=K13,K19）
	@mkdir -p runs
	cd ai && uv run python evals/run_kb_answer.py --json ../runs/kb-latest.json $${ONLY:+--only $$ONLY}

critic-lab:      ## 破坏实验 B：往 Agent 回答里塞假数字看 Critic 抓不抓得住（REFRESH=1 重新让 Agent 答题）
	cd ai && uv run python evals/critic_lab.py $${REFRESH:+--refresh}

runs-kb:         ## 知识库问答连跑 N 次  用法: make runs-kb N=3 G=kb-top8（G 是存档名前缀）
	@mkdir -p runs
	@for i in $$(seq 1 $${N:-3}); do \
		echo "=== 第 $$i 次 ==="; \
		(cd ai && uv run python evals/run_kb_answer.py --json ../runs/$${G:-kb}-$$i.json | tail -7) || true; \
	done

agg:             ## 聚合多次评测  用法: make agg G=after   (读 runs/after-*.json)
	@cd ai && uv run python evals/aggregate.py ../runs/$${G:-after}-*.json --name "$${G:-after}"

agg-vs:          ## 对比两组  用法: make agg-vs A=base B=after
	@cd ai && uv run python evals/aggregate.py ../runs/$${A:-base}-*.json --name "$${A:-base}" \
		--vs ../runs/$${B:-after}-*.json --vs-name "$${B:-after}"

runs:            ## 连跑 N 次并存档  用法: make runs G=after N=3
	@mkdir -p runs
	@for i in $$(seq 1 $${N:-3}); do \
		$(MAKE) --no-print-directory eval WEEK=$${WEEK:-2} || true; \
		cp eval-report.json runs/$${G:-after}-$$i.json; \
	done
	@$(MAKE) --no-print-directory agg G=$${G:-after}

lint:
	cd ai && uv run ruff check src evals solutions

gateway:         ## 起 .NET 网关（W5）
	cd gateway/ChemQ.Gateway && dotnet run

clean:           ## 清掉缓存和临时产物
	rm -rf ai/.venv ai/.pytest_cache ai/.ruff_cache eval-report*.json
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
