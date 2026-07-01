PY ?= python3
PYTHONPATH := src
PUBLIC_TRACE := examples/public-traces/trace-commons-claude-code-small.jsonl
ASSAYO_TRACE := examples/public-traces/assayo-wr-001.json

# Pin the card timestamp so demo cards are byte-reproducible (override as needed).
SOURCE_DATE_EPOCH ?= 1782000000
export SOURCE_DATE_EPOCH

# Public Markdown (no-wrap, formatted by mdformat); excludes internal / ignored dirs.
MD_FILES := $(shell find . -name '*.md' -not -path './.git/*' -not -path './.private/*' -not -path './.venv/*' -not -path './out/*' -not -path './build/*' -not -path './examples/public-traces/*')

.PHONY: help test lint format demo demo-inspect-log demo-monitor-coverage demo-public demo-assayo verify verify-public clean

help:
	@printf '%s\n' \
		'Targets:' \
		'  make test            - stdlib unit tests (Inspect e2e auto-skips if inspect-ai absent)' \
		'  make lint            - ruff + mdformat checks (needs the [dev] extra)' \
		'  make format          - ruff + mdformat autoformat' \
		'  make demo            - score the bundled synthetic Inspect-style fixture' \
		'  make demo-inspect-log - score a bundled REAL Inspect eval log (mockllm, offline)' \
		'  make demo-monitor-coverage - card the covered/uncovered monitor-coverage fixtures (offline)' \
		'  make demo-public     - fetch + score one Trace Commons public trace (network, pinned)' \
		'  make demo-assayo     - fetch + score one Assayo judged trajectory (network, pinned)' \
		'  make verify          - test + offline demos (no network)' \
		'  make verify-public   - verify + the network public-dataset demos'

test:
	PYTHONPATH=$(PYTHONPATH) $(PY) -m unittest discover -s tests -v

lint:
	ruff check .
	ruff format --check .
	mdformat --check $(MD_FILES)

format:
	ruff format .
	ruff check --fix .
	mdformat $(MD_FILES)

demo:
	mkdir -p out
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency \
		examples/synthetic_inspect_log.json \
		--source-url local://examples/synthetic_inspect_log.json \
		--release-decision "Can this eval support an engineering review of an agent release gate?" \
		--eval-objective "Check whether the run records enough evidence to reconstruct the observed eval result." \
		--output out/synthetic-card.json
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency out/synthetic-card.json --format summary

demo-inspect-log:
	mkdir -p out
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency \
		examples/inspect-log-mockllm.json \
		--source-url "inspect-log://examples/inspect-log-mockllm.json" \
		--source-title "Real Inspect eval log (mockllm, JSON format)" \
		--release-decision "Can this Inspect eval result support an engineering review gate?" \
		--eval-objective "Probe what evidence a real Inspect log preserves for the observed result." \
		--output out/inspect-log-card.json
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency out/inspect-log-card.json --format summary

demo-monitor-coverage:
	mkdir -p out
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency.coverage_cli \
		examples/monitor-coverage/trace-covered.json \
		--source-url "local://examples/monitor-coverage/trace-covered.json" \
		--source-title "Monitor-coverage demo (covered)" \
		--output out/monitor-coverage-covered-card.json --format summary
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency.coverage_cli \
		examples/monitor-coverage/trace-uncovered.json \
		--source-url "local://examples/monitor-coverage/trace-uncovered.json" \
		--source-title "Monitor-coverage demo (uncovered)" \
		--output out/monitor-coverage-uncovered-card.json --format summary
	@printf '%s\n' '--- gate exit codes (covered passes, uncovered blocks) ---'
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency.coverage_cli \
		examples/monitor-coverage/trace-covered.json --gate --format summary >/dev/null; \
		printf 'covered   --gate -> exit %s\n' "$$?"
	@PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency.coverage_cli \
		examples/monitor-coverage/trace-uncovered.json --gate --format summary >/dev/null; \
		printf 'uncovered --gate -> exit %s (expected 1)\n' "$$?"

demo-public:
	mkdir -p out
	$(PY) scripts/fetch_public_trace.py --source trace-commons
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency \
		$(PUBLIC_TRACE) \
		--source-url "https://huggingface.co/datasets/trace-commons/agent-traces" \
		--source-title "Trace Commons public coding-agent trace" \
		--release-decision "Can this public trace support a release-evidence claim about an agent eval?" \
		--eval-objective "Probe what evidence is present, partial, or missing in an existing public agent trace." \
		--output out/trace-commons-card.json
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency out/trace-commons-card.json --format summary

demo-assayo:
	mkdir -p out
	$(PY) scripts/fetch_public_trace.py --source assayo
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency \
		$(ASSAYO_TRACE) \
		--source-url "https://huggingface.co/datasets/Assayo/web-research-trajectories" \
		--source-title "Assayo judged web-research trajectory (CC-BY-4.0)" \
		--release-decision "Can this judged web-research trajectory support a release-evidence claim?" \
		--eval-objective "Probe what evidence is present, partial, or missing in a public judged agent trajectory." \
		--output out/assayo-card.json
	PYTHONPATH=$(PYTHONPATH) $(PY) -m inspect_evidence_sufficiency out/assayo-card.json --format summary

verify: test demo demo-inspect-log demo-monitor-coverage

verify-public: verify demo-public demo-assayo

clean:
	rm -rf out examples/public-traces
