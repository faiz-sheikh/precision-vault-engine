"""Student-first Streamlit application. Passive render paths never invoke providers."""

from __future__ import annotations

from typing import Any

from pve.product import (
    ARTIFACT_LABELS,
    WORKFLOWS,
    Depth,
    ProductError,
    ProductMode,
    ProductService,
    StalePreview,
)
from pve.settings import Settings
from pve.store import Store

SIMPLE_SECTIONS = (
    "Home",
    "Study",
    "Generate Notes",
    "Research",
    "Quiz",
    "Viva Practice",
    "Revision",
    "Full Study Pack",
    "My Materials",
    "Progress",
    "System Status",
    "Help",
)
EXPERT_SECTIONS = (
    *SIMPLE_SECTIONS,
    "Jobs",
    "Provider Ledger",
    "Budgets & Routing",
    "Manifest & Sources",
    "Artifacts & Dependencies",
    "Quality & Grounding",
    "Failures & Recovery",
    "Diagnostics",
    "Advanced Settings",
)
SECTIONS = EXPERT_SECTIONS  # Compatibility export for technical tests/tools.


def snapshot(store: Store) -> dict[str, Any]:
    """Read-only operational projection. It cannot construct a provider adapter."""
    with store.connect() as db:
        def scalar(query: str) -> int:
            return int(db.execute(query).fetchone()[0])

        last = db.execute("SELECT created_at FROM runs WHERE status='SUCCEEDED' ORDER BY created_at DESC LIMIT 1").fetchone()
        return {
            "runs": scalar("SELECT count(*) FROM runs"),
            "jobs": scalar("SELECT count(*) FROM jobs"),
            "provider_operations": scalar("SELECT count(*) FROM provider_operations"),
            "artifacts": scalar("SELECT count(*) FROM artifacts"),
            "sources": scalar("SELECT count(*) FROM sources"),
            "failures": scalar("SELECT count(*) FROM failures"),
            "last_success": last[0] if last else "No completed runs yet",
            "sections": SECTIONS,
            "provider_calls": 0,
        }


def _init_state(st: Any) -> None:
    defaults = {
        "pve_mode": ProductMode.SIMPLE.value,
        "pve_onboarding_complete": False,
        "pve_onboarding_step": 1,
        "pve_preview": None,
        "pve_last_result": None,
        "pve_page": "Home",
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _status_label(configured: bool, live: bool) -> str:
    if live:
        return "Ready (explicitly enabled)"
    if configured:
        return "Configured — live access disabled"
    return "Not configured — Demo Mode is available"


def _render_onboarding(st: Any, service: ProductService) -> None:
    step = int(st.session_state.pve_onboarding_step)
    st.title("Welcome to Precision Vault Engine")
    st.caption(f"First-time setup · Step {step} of 5")
    if step == 1:
        st.write("PVE creates and organizes verified study materials while protecting your work and avoiding unnecessary AI requests.")
        st.info("You can explore a complete study workflow in Demo Mode. No API key, network inference, or provider quota is needed.")
    elif step == 2:
        health = service.doctor()
        st.subheader("System health")
        st.write(f"✅ Database: {health['database']}")
        st.write(f"✅ Storage: {health['storage']}")
        st.write(f"✅ Curriculum: {health['manifest']}")
        st.write(f"✅ Demo material: {health['demo']}")
    elif step == 3:
        st.subheader("AI provider setup")
        for name, state in service.doctor()["providers"].items():
            st.write(f"**{name}** — {_status_label(state['configured'], state['live'])}")
        st.caption("PVE shows only whether credentials exist. Secret values are never displayed, and this check makes no network request.")
    elif step == 4:
        st.subheader("Choose how you want to use PVE")
        choice = st.radio("Operating mode", [ProductMode.DEMO.value, ProductMode.SIMPLE.value, ProductMode.EXPERT.value], help="Changing mode changes what you can see. It never enables providers or raises budgets.")
        st.session_state.pve_mode = choice
    else:
        st.subheader("Choose your first action")
        st.write("Demo Mode is the safest way to learn the workflow.")
        if st.button("Try Demo Study Pack", type="primary", use_container_width=True):
            st.session_state.pve_mode = ProductMode.DEMO.value
            st.session_state.pve_page = "Full Study Pack"
            st.session_state.pve_onboarding_complete = True
            st.rerun()
        if st.button("Browse Existing Materials", use_container_width=True):
            st.session_state.pve_page = "My Materials"
            st.session_state.pve_onboarding_complete = True
            st.rerun()
        if st.button("Open Dashboard", use_container_width=True):
            st.session_state.pve_page = "Home"
            st.session_state.pve_onboarding_complete = True
            st.rerun()
    left, right = st.columns(2)
    if step > 1 and left.button("Back"):
        st.session_state.pve_onboarding_step = step - 1
        st.rerun()
    if step < 5 and right.button("Continue", type="primary"):
        st.session_state.pve_onboarding_step = step + 1
        st.rerun()


def _render_home(st: Any, service: ProductService, data: dict[str, Any]) -> None:
    st.title("Precision Vault Engine")
    mode = ProductMode(st.session_state.pve_mode)
    health = service.doctor()
    state = "Ready for Demo" if mode is ProductMode.DEMO else "Offline-safe"
    st.success(f"PVE Status: {state}")
    a, b, c = st.columns(3)
    a.metric("My materials", data["artifacts"])
    b.metric("Pending failures", data["failures"])
    c.metric("Last successful run", data["last_success"])
    st.subheader("What would you like to create?")
    actions = ("Generate Notes", "Create Quiz", "Practice Viva", "Research Topic", "Full Study Pack", "Explore Demo")
    columns = st.columns(2)
    destinations = {"Create Quiz": "Quiz", "Practice Viva": "Viva Practice", "Research Topic": "Research", "Explore Demo": "Full Study Pack"}
    for index, action in enumerate(actions):
        if columns[index % 2].button(action, use_container_width=True):
            if action == "Explore Demo":
                st.session_state.pve_mode = ProductMode.DEMO.value
            st.session_state.pve_page = destinations.get(action, action)
            st.rerun()
    with st.expander("System readiness"):
        st.write(f"Curriculum: {health['manifest']}")
        st.write(f"Database: {health['database']} · Storage: {health['storage']}")
        for name, provider in health["providers"].items():
            st.write(f"{name}: {_status_label(provider['configured'], provider['live'])}")


def _workflow_for_page(page: str) -> str:
    return {
        "Generate Notes": "Generate Notes", "Research": "Research Topic", "Quiz": "Create Quiz",
        "Viva Practice": "Practice Viva", "Revision": "Create Revision Sheet",
        "Full Study Pack": "Full Study Pack", "Study": "Generate Notes",
    }.get(page, "Generate Notes")


def _render_preview(st: Any, preview: Any) -> None:
    command = preview.command
    st.subheader("Review your plan")
    st.write(f"**Module:** {command.module_id}")
    st.write(f"**Depth:** {command.depth.value}")
    st.write("**PVE will create:**")
    for item in command.requested_artifacts:
        st.write(f"✓ {ARTIFACT_LABELS[item]}")
    st.write("**AI services expected:** " + (", ".join(command.provider_capabilities_required) if not command.demo else "None — bundled demo material"))
    st.write(f"**Estimated external requests:** {preview.estimated_requests_min}-{preview.estimated_requests_max}")
    st.write(f"**Estimated cost/quota:** {preview.estimated_cost}")
    st.write(f"**Reusable work:** {preview.reusable_items} items")
    st.write(f"**New AI operations expected:** {preview.new_operations}")
    if command.demo:
        st.info("DEMO OUTPUT · No external AI provider will be contacted.")


def _render_workflow(st: Any, service: ProductService, page: str) -> None:
    workflow = _workflow_for_page(page)
    st.title(page)
    st.write("Choose a module and review the plan before anything runs.")
    natural = st.text_input("Or describe what you want", placeholder="Create detailed notes and a 20-question quiz for PID control", help="PVE converts this locally into a validated plan. Typing here never contacts an AI provider.")
    if natural and st.button("Understand my request"):
        try:
            st.session_state.pve_preview = service.preview_from_text(natural, demo=st.session_state.pve_mode == ProductMode.DEMO.value)
        except ProductError as exc:
            st.warning(str(exc))
    with st.form(f"workflow_{page}"):
        module = st.text_input("Subject or module", value="PID Control" if st.session_state.pve_mode == ProductMode.DEMO.value else "")
        selected = st.selectbox("What should PVE create?", list(WORKFLOWS), index=list(WORKFLOWS).index(workflow))
        depth = st.selectbox("Study depth", [item.value for item in Depth], index=1, help="Quick is concise; Standard balances detail; Deep includes more explanation and checks.")
        submitted = st.form_submit_button("Build safe preview", type="primary", use_container_width=True)
    if submitted:
        try:
            st.session_state.pve_preview = service.create_preview(module_id=module, artifacts=WORKFLOWS[selected], depth=Depth(depth), intent=selected, demo=st.session_state.pve_mode == ProductMode.DEMO.value)
        except ProductError as exc:
            st.warning(str(exc))
    preview = st.session_state.pve_preview
    if preview is not None:
        _render_preview(st, preview)
        run, edit, cancel = st.columns(3)
        if run.button("Run this plan", type="primary", use_container_width=True):
            if not preview.command.demo:
                st.warning("Live workflows remain disabled. Switch to Demo Mode to complete this walkthrough without provider calls.")
            else:
                try:
                    with st.status("Building your demo study pack", expanded=True) as status:
                        for message in ("Preparing your material ✓", "Finding bundled reliable sources ✓", "Writing your notes ✓", "Checking completeness ✓", "Checking source support ✓", "Creating practice material ✓", "Finalizing study pack ✓"):
                            st.write(message)
                        result = service.execute_demo(preview.command.preview_id)
                        status.update(label="Study pack ready", state="complete")
                    st.session_state.pve_last_result = result
                    st.success("Your study pack is ready in My Materials. No external AI provider was contacted.")
                except StalePreview as exc:
                    st.warning(str(exc))
        if edit.button("Edit plan", use_container_width=True):
            st.session_state.pve_preview = None
            st.rerun()
        if cancel.button("Cancel preview", use_container_width=True):
            st.session_state.pve_preview = None
            st.info("Preview cancelled. No provider was contacted.")


def _render_library(st: Any, service: ProductService) -> None:
    st.title("My Materials")
    st.write("Search and open your saved study materials. This search is local and makes no AI requests.")
    query = st.text_input("Search materials", placeholder="PID control, quiz, notes…")
    kind = st.selectbox("Material type", ["All", *ARTIFACT_LABELS])
    items = service.materials(query, kind)
    if not items:
        st.info("No matching materials yet. Try the Demo Study Pack to create your first set.")
        return
    titles = [f"{item['title']} · {item['created_at'][:10]}" for item in items]
    chosen = st.selectbox("Open material", range(len(items)), format_func=lambda i: titles[i])
    item = items[chosen]
    st.subheader(item["title"])
    if item.get("demo"):
        st.info("DEMO OUTPUT · No external AI provider was contacted.")
    st.markdown(item.get("content", "Content preview is unavailable for this legacy artifact."))
    q, g, v = st.columns(3)
    q.metric("Quality", item["quality_status"])
    g.metric("Source support", item["grounding_status"])
    v.metric("Version", item["version"])
    with st.expander("Sources and related details"):
        for source in item.get("sources", []):
            st.write(f"• {source}")
        if st.session_state.pve_mode == ProductMode.EXPERT.value:
            st.json({key: item.get(key) for key in ("id", "content_hash", "dependency_fingerprint", "generation_id")})


def _render_status(st: Any, service: ProductService, data: dict[str, Any]) -> None:
    st.title("PVE Doctor")
    health = service.doctor()
    st.write("✅ Database healthy")
    st.write(f"✅ Artifact storage: {health['storage']}")
    st.write(f"✅ Demo mode: {health['demo']}")
    st.write(f"✅ Manifest: {health['manifest']}")
    for name, provider in health["providers"].items():
        symbol = "✅" if provider["configured"] else "⚠️"
        st.write(f"{symbol} {name}: {_status_label(provider['configured'], provider['live'])}")
    st.caption(f"Saved materials: {data['artifacts']} · Recorded provider operations: {data['provider_operations']} · This page made 0 provider calls.")


def _render_help(st: Any) -> None:
    st.title("Help")
    topics = {
        "What is PVE?": "A study application that creates, checks, preserves, and organizes learning material.",
        "How do I start?": "Choose Demo Mode, open Full Study Pack, review the plan, and select Run this plan.",
        "What is Demo Mode?": "A complete offline walkthrough using bundled deterministic material and zero provider quota.",
        "What is a Study Pack?": "Research, notes, revision material, a quiz, viva practice, and flashcards for one module.",
        "Why does PVE show a preview?": "The preview makes scope, expected services, reusable work, and request bounds visible before execution.",
        "What are AI providers?": "External services that can generate or review material. PVE controls when they may be used.",
        "Why might a provider be unavailable?": "It may be unconfigured, live-disabled, rate-limited, or blocked by account access or quota.",
        "What does blocked mean?": "PVE stopped safely and will not continue until the underlying issue and required approval are resolved.",
        "Where are my materials?": "Open My Materials from the sidebar.",
        "How do I safely resume a failed run?": "Open Progress or Failures & Recovery. PVE offers Resume Safely only when controller policy permits it.",
    }
    for question, answer in topics.items():
        with st.expander(question):
            st.write(answer)


def _render_expert(st: Any, service: ProductService, page: str) -> None:
    queries = {
        "Jobs": "SELECT id,phase,state,lease_owner,fencing_token,created_at FROM jobs ORDER BY created_at DESC LIMIT 100",
        "Provider Ledger": "SELECT id,provider,model,capability,state,attempts,estimated_cost_usd,actual_cost_usd,created_at FROM provider_operations ORDER BY created_at DESC LIMIT 100",
        "Manifest & Sources": "SELECT id,checksum,status,created_at FROM manifest_candidates ORDER BY created_at DESC LIMIT 100",
        "Artifacts & Dependencies": "SELECT id,module_id,phase,content_hash,dependency_fingerprint,status,created_at FROM artifacts ORDER BY created_at DESC LIMIT 100",
        "Failures & Recovery": "SELECT id,scope_type,scope_id,classification,detail,created_at FROM failures ORDER BY created_at DESC LIMIT 100",
    }
    st.title(page)
    st.warning("Expert view shows operational evidence. Visibility does not grant approval or change provider, budget, manifest, or source authority.")
    if page == "Diagnostics":
        _render_status(st, service, snapshot(service.store))
    elif page in queries:
        with service.store.connect() as db:
            st.dataframe([dict(row) for row in db.execute(queries[page])], use_container_width=True)
    elif page == "Budgets & Routing":
        st.json({"max_requests_per_run": service.settings.max_requests_per_run, "max_cost_usd": service.settings.max_cost_usd, "routes": {key: value.__dict__ for key, value in service.settings.routes.items()}})
    elif page == "Quality & Grounding":
        st.info("Quality and grounding evidence appears on each artifact and remains enforced by the existing pipeline gates.")
    else:
        st.info("Dangerous changes require their dedicated controller authorization and explicit confirmation. This dashboard does not auto-enable them.")


def render(store: Store, settings: Settings | None = None) -> None:
    import streamlit as st

    active_settings = settings or Settings(state_dir=store.path.parent, artifact_dir=store.path.parent / "artifacts")
    service = ProductService(active_settings)
    _init_state(st)
    st.set_page_config(page_title="Precision Vault Engine", page_icon="📚", layout="wide", initial_sidebar_state="expanded")
    if not st.session_state.pve_onboarding_complete:
        _render_onboarding(st, service)
        return
    mode = st.sidebar.selectbox("Experience", [item.value for item in ProductMode], index=[item.value for item in ProductMode].index(st.session_state.pve_mode), help="Mode changes visibility only. It cannot enable providers, raise budgets, approve retries, or promote manifests.")
    st.session_state.pve_mode = mode
    pages = EXPERT_SECTIONS if mode == ProductMode.EXPERT.value else SIMPLE_SECTIONS
    preferred = st.session_state.pve_page if st.session_state.pve_page in pages else "Home"
    page = st.sidebar.radio("Go to", pages, index=pages.index(preferred))
    st.session_state.pve_page = page
    st.sidebar.caption("Live providers remain controlled by PVE safety settings.")
    data = snapshot(store)
    if page == "Home":
        _render_home(st, service, data)
    elif page in {"Study", "Generate Notes", "Research", "Quiz", "Viva Practice", "Revision", "Full Study Pack"}:
        _render_workflow(st, service, page)
    elif page == "My Materials":
        _render_library(st, service)
    elif page in {"System Status", "Progress"}:
        _render_status(st, service, data)
    elif page == "Help":
        _render_help(st)
    else:
        _render_expert(st, service, page)


def main() -> None:
    settings = Settings.from_env()
    store = Store(settings.database_path)
    store.initialize()
    render(store, settings)


if __name__ == "__main__":
    main()
