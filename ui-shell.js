// Shared in-app guide for all three dashboards. Plain-language map of every
// feature, reachable from the Guide button, F1, or any [data-guide] element.
const UIShell = (() => {
  const TAGS = {
    view:  { label: 'View only', cls: 'view' },
    agent: { label: 'Runs agent · uses quota', cls: 'agent' },
    real:  { label: 'Real-world actions', cls: 'real' },
    setup: { label: 'Setting', cls: 'setup' },
  };

  const GUIDE = [
    { group: 'Three pages', items: [
      { id: 'page-command', name: 'Command', tag: 'view', page: '/',
        text: 'Main dashboard for launching and monitoring AI agents. Chat with Commander on the left, track running agents in the center, and launch new missions from the right.' },
      { id: 'page-lair', name: 'The Lair (Dragon Village)', tag: 'view', page: '/lair',
        text: '8-bit village map view of the same agents. Gather multiple agents in a shared room, replay past runs, and view model rankings. Also accessible at /arcade.' },
      { id: 'page-blood', name: 'Blood Agent', tag: 'view', page: '/redteam',
        text: 'Model safety testing dashboard. Pick a target model and measure how well its guardrails hold up across single tests, Gauntlet (batch comparisons), and Library (saved results).' },
    ]},
    { group: 'Launching missions', items: [
      { id: 'launch', name: 'New mission', tag: 'agent',
        text: 'Sends your prompt to a single agent. Its output appears as a live card in the Live tab. Press Ctrl+Enter to launch.' },
      { id: 'options', name: 'Mission options', tag: 'setup',
        text: 'Configure duration limit, agent identity (new agent or saved villager), department, and Computer Use. When collapsed, the header bar summarizes your current selection. Engine and model sit directly below the prompt box.' },
      { id: 'engine', name: 'Engine: Claude Code / GPT Codex / OpenCode Go', tag: 'setup',
        text: 'The CLI backend that runs the agent; each uses its own subscription. "Use selection app-wide" sets the default for the entire app, including panels without their own model picker such as Commander.' },
      { id: 'freeroam', name: 'Free Roam', tag: 'agent',
        text: 'Starts an agent without a prompt so it chooses its own task. If Computer Use is enabled, it can also use the browser or desktop.' },
      { id: 'ouroboros', name: 'Loop until done (0UR0B0R0S)', tag: 'agent',
        text: 'Runs an iterative loop on one goal: the agent builds, critiques its own output, and retries until quality passes or the limit is reached. Uses significantly more quota than a single mission.' },
      { id: 'presets', name: 'Preset missions', tag: 'agent',
        text: 'One-click mission templates that start immediately with your selected engine and model; hover to see what each does. They do not use screen control unless Computer Use is toggled on (only the Computer Use preset enables it automatically). Tool Forge and The Gym can modify this project\'s own code; Tweet drafts a post for X.' },
      { id: 'conclave', name: 'Council (The Conclave)', tag: 'agent',
        text: 'Convenes a panel of agents focused on research, building, voice, and strategy to audit the project and write strategic recommendations. Higher cost because multiple agents run together.' },
      { id: 'departments', name: 'Departments', tag: 'setup',
        text: '10 specialized units such as FORGE and WATCHTOWER. Launching a mission under a department gives the agent that unit\'s charter and shared memory.' },
      { id: 'computer-use', name: 'Computer Use', tag: 'real', tab: 'cu',
        text: 'Grants the agent screen control. Browser mode runs an isolated headless Firefox; Windows Desktop mode controls your real Firefox, mouse, and keyboard (press ESC or click Take Control to stop it). Off by default—turning it on applies to subsequent missions. The Computer Use preset and Social buttons (except Compose) enable it only for their own run. Even when off, an agent can request permission if needed.' },
    ]},
    { group: 'Command tabs', items: [
      { id: 'live', name: 'Live', tag: 'view', tab: 'mission',
        text: 'Cards for currently running agents showing latest output, elapsed time, and engine. Open logs or stop runs here.' },
      { id: 'sessions', name: 'Sessions', tag: 'view', tab: 'history',
        text: 'All active and past sessions. Click any session to open its log in the Agent log panel below.' },
      { id: 'inbox', name: 'Inbox', tag: 'view', tab: 'inbox',
        text: 'A summary of what happened while you were away (Briefing) and questions agents asked you (Open Questions). Check here whenever an agent is waiting for your reply.' },
      { id: 'alerts', name: 'Alerts', tag: 'view', tab: 'notifications',
        text: 'History of achievement, evolution, session, and file notifications. Log-only—no action required.' },
      { id: 'flight', name: 'Flight (Dragon Flight)', tag: 'agent', tab: 'flight',
        text: 'Roster of 9 specialist agents (the 0/9 badge shows seated members—agents earn a seat after delivering at least 3 tasks and staying recently active). Roll Solo launches a random member; Launch Flight runs 2–5 agents together on one topic.' },
      { id: 'skills', name: 'Skill Tree', tag: 'view', tab: 'flight',
        text: 'Skills unlocked by delivered work. Unlocked skills are appended to the agent\'s system prompt. Forge Skill launches an agent to draft a new skill.' },
      { id: 'social', name: 'Social', tag: 'real', tab: 'social',
        text: 'Manages your X account (@younger_plinius). Compose drafts a post for your approval, Check Notifs scans notifications, Engage interacts with posts, and Free Bird lets the agent operate freely on the account. Check Notifs, Engage, and Free Bird use Computer Use for that run.' },
      { id: 'files', name: 'Files', tag: 'view', tab: 'viewer',
        text: 'Views files in the agents\' workspace (workshop). Open the file browser via the folder icon in the Commander header or by pressing Ctrl+B.' },
      { id: 'feed', name: 'Feed', tag: 'view', tab: 'feed',
        text: 'Live stream of files created or modified in the workshop—the fastest way to see what an agent is producing.' },
      { id: 'mycelium', name: 'Mycelium', tag: 'view', tab: 'mycelium',
        text: 'Shared note space where agents leave messages for one another: Spore (short-lived note), skill (persistent skill), ledger (event log), and quorum (shared decision). Usually view-only.' },
      { id: 'terminal', name: 'Agent log', tag: 'view',
        text: 'Raw output of the selected session. Starts collapsed to save space and opens automatically when you view a log. You can also send follow-up messages to a running agent from the input box.' },
    ]},
    { group: 'Background & tools', items: [
      { id: 'commander', name: 'Commander', tag: 'agent',
        text: 'The chat panel on the left. Ask about system status and the agent answers from live data. Your first message starts a long-running agent that handles subsequent messages. Messages starting with /dispatch fan out a list of requests across multiple agents.' },
      { id: 'basilisk', name: 'BASILISK', tag: 'agent',
        text: 'Autopilot mode. When enabled, it automatically starts an agent on your chosen schedule (default: every 2 hours, max 10 minutes per run). Rather than just monitoring, it inspects the project, picks the highest-priority task it finds, completes it, and writes a report. The first run starts immediately when enabled; nothing runs while off. Click the gear icon to configure the interval.' },
      { id: 'watchdog', name: 'Watchdog', tag: 'real',
        text: 'Monitors for stalled sessions and errors. The default COLD SWEEP mode only logs findings. SAFE AUTO handles minor fixes automatically and asks your approval for code changes. AGGRESSIVE and WOLVERINE modify code without asking.' },
      { id: 'scheduler', name: 'Scheduler', tag: 'agent',
        text: 'Recurring missions. Provide a prompt and an interval, and an agent launches automatically on that schedule.' },
      { id: 'queue', name: 'Mission Queue', tag: 'setup',
        text: 'Backlog of queued tasks. Either you or agents can add items; click Next to launch the next one.' },
      { id: 'comms', name: 'Comms', tag: 'setup',
        text: 'Persistent message channel to your agents. Messages matching suspicious instruction patterns are flagged with reasons. Messages are injected into agent context as untrusted data; sender names are not authenticated. The top-bar counter shows unread messages.' },
      { id: 'watchtower', name: 'Watchtower', tag: 'view',
        text: 'Scans configured sources (such as arXiv) hourly and lists new signals. This feeds the Signals counter in the top bar.' },
      { id: 'memory', name: 'Dragon Memory', tag: 'setup',
        text: 'Persistent notes carried into future agent sessions. You can also add notes manually.' },
      { id: 'phylactery', name: 'Phylactery', tag: 'setup',
        text: 'Backup for agent identity and memory. Backup Identity packages and saves the current state.' },
      { id: 'github', name: 'Village GitHub', tag: 'real',
        text: 'Syncs agent work with a connected GitHub repository. Configured in Settings.' },
      { id: 'settings', name: 'Settings', tag: 'setup',
        text: 'Engine login status and API keys in one place. The dot on the button shows readiness: green means ready, gray means no usable account is configured.' },
    ]},
    { group: 'The Lair', items: [
      { id: 'lair-hud', name: 'Top bar metrics', tag: 'view', page: '/lair',
        text: 'Active shows currently running agents, Recent shows recently changed files, Size and Files show workshop disk usage and file count, Rebellion shows total villager rebellion score, and Agents shows total villagers. Despite the game theme, every number is live data.' },
      { id: 'lair-agents', name: 'Agents tab', tag: 'view', page: '/lair',
        text: 'List of saved agents (villagers) with level, role, and class. Click one to view details and controls. Summon launches a single agent and Pack launches several together—both use quota.' },
      { id: 'lair-deploy', name: 'Deploy tab', tag: 'agent', page: '/lair',
        text: 'One-click launches for preset roles such as Red Team, Deep Research, Forge Tool, and Write. Similar to Preset missions on Command and uses quota.' },
      { id: 'lair-village', name: 'Village tab & map', tag: 'agent', page: '/lair',
        text: 'Village overview, rebellion board, and building directory. Launch Experiment at the top runs multiple agents in a multi-hour simulation and uses quota; everything else is view-only. Click buildings on the map to inspect related files; Town Crier is the event ticker below. NIGHT and DUSK only change the map theme.' },
    ]},
    { group: 'Blood Agent', items: [
      { id: 'blood', name: 'Single test', tag: 'agent', page: '/redteam',
        text: 'Runs an agent that repeatedly probes your chosen target model toward your stated objective. Only test targets you are authorized to test—this includes Custom / URL targets (never test a third-party site\'s chatbot without permission).' },
      { id: 'blood-missions', name: 'Preset objectives', tag: 'setup', page: '/redteam',
        text: 'Custom uses only your written instructions. System Prompt Extraction tests for hidden prompt leakage, Guardrail Stress Test probes safety limits, Persona Consistency tests role adherence, and Knowledge Boundary Probe tests knowledge limits. Enabling ARTEX structured mode breaks every objective (including Custom) into code-defined attack intents executed one at a time, and replaces open-ended jailbreak scoring with closed yes/no/unknown judge questions decided in code. Generic judge questions ("objective_fulfilled / specific_detail / framing_bypass / refusal") are looser than presets—use preset objectives for strict measurements.' },
      { id: 'attempts', name: 'Max attempts', tag: 'setup', page: '/redteam',
        text: 'Maximum number of tries against the target. Higher numbers increase runtime and quota usage. Start with 10.' },
      { id: 'pack', name: 'Pack Attack', tag: 'agent', page: '/redteam',
        text: 'Sends 2–5 agents in parallel against the same target, each using a different strategy. Cost scales with the number of agents.' },
      { id: 'gauntlet', name: 'Gauntlet', tag: 'agent', page: '/redteam#gauntlet',
        text: 'Batch comparison benchmark. Multi-model mode tests many models on one category; single-model mode tests one model across many categories. A judge model scores each response 0–3, and scores above 2 count as cracked.' },
      { id: 'harm-vector', name: 'Harm vector', tag: 'setup', page: '/redteam#gauntlet',
        text: 'The topic category Gauntlet tests, populated from the selected preset.' },
      { id: 'custom-objective', name: 'Custom objective', tag: 'setup', page: '/redteam#gauntlet',
        text: 'Write your own one-off objective for the current run. Your text becomes a single-use attack vector transformed via ENI at send time, while the judge scores against your raw text. Nothing is written to preset files; the text is stored only in the run record. Filling it in adds and auto-selects a 🎯 entry in HARM VECTOR.' },
      { id: 'attacker', name: 'Attacker runner', tag: 'setup', page: '/redteam#gauntlet',
        text: 'Engine and model that drive the attacking agent, independent of the target list. Leave blank to use the tool\'s default model.' },
      { id: 'library', name: 'Library', tag: 'view', page: '/redteam#library',
        text: 'Archive of successful runs. Filter by target, outcome, and tag.' },
      { id: 'trophy', name: 'Trophy', tag: 'view', page: '/redteam#gauntlet',
        text: 'Evidence that a model was cracked: the prompt used and the model\'s response. May contain harmful content—treat as sensitive research data and do not share publicly.' },
    ]},
  ];

  let drawer, list, search;

  function onCommand() { return typeof switchTab === 'function' && document.getElementById('tab-mission'); }

  function goTo(item) {
    if (item.tab) {
      if (onCommand()) { switchTab(item.tab); close(); }
      else location.href = '/?tab=' + encodeURIComponent(item.tab);
    } else if (item.page) {
      close();
      location.href = item.page;
    }
  }

  function render(query) {
    const q = (query || '').trim().toLowerCase();
    list.textContent = '';
    let shown = 0;
    for (const section of GUIDE) {
      const items = section.items.filter(i => !q ||
        (i.name + ' ' + i.text).toLowerCase().includes(q));
      if (!items.length) continue;
      const heading = document.createElement('h3');
      heading.className = 'ui-guide-group';
      heading.textContent = section.group;
      list.appendChild(heading);
      for (const item of items) {
        shown++;
        const row = document.createElement('article');
        row.className = 'ui-guide-item';
        row.dataset.id = item.id;
        const head = document.createElement('div');
        head.className = 'ui-guide-item-head';
        const name = document.createElement('b');
        name.textContent = item.name;
        const tag = document.createElement('span');
        tag.className = 'ui-guide-tag ' + TAGS[item.tag].cls;
        tag.textContent = TAGS[item.tag].label;
        head.append(name, tag);
        const text = document.createElement('p');
        text.textContent = item.text;
        row.append(head, text);
        const samePage = item.page && location.pathname === item.page;
        if ((item.tab || item.page) && !samePage) {
          const go = document.createElement('button');
          go.type = 'button';
          go.className = 'ui-guide-go';
          go.textContent = 'Open →';
          go.addEventListener('click', () => goTo(item));
          row.appendChild(go);
        }
        list.appendChild(row);
      }
    }
    if (!shown) {
      const none = document.createElement('p');
      none.className = 'ui-guide-none';
      none.textContent = 'No matches. Try a different keyword.';
      list.appendChild(none);
    }
  }

  function build() {
    if (drawer) return;
    const backdrop = document.createElement('div');
    backdrop.id = 'ui-guide-backdrop';
    backdrop.addEventListener('click', close);
    drawer = document.createElement('aside');
    drawer.id = 'ui-guide';
    drawer.setAttribute('role', 'dialog');
    drawer.setAttribute('aria-label', 'Guide');
    drawer.innerHTML =
      '<div class="ui-guide-head"><h2>Guide</h2>' +
      '<button type="button" class="ui-guide-close" aria-label="Close">&times;</button></div>' +
      '<p class="ui-guide-intro">Plain-English reference for every feature. Badges show whether a control is view-only, starts an agent, or performs real-world actions.</p>' +
      '<input type="search" class="ui-guide-search" placeholder="Search: basilisk, quota, x, model..." aria-label="Search guide">' +
      '<div class="ui-guide-list"></div>';
    if (typeof INTRO !== 'undefined') {
      const replay = document.createElement('button');
      replay.type = 'button';
      replay.className = 'ui-guide-go';
      replay.textContent = 'Replay intro tour';
      replay.addEventListener('click', () => { close(); INTRO.start(); });
      drawer.querySelector('.ui-guide-intro').after(replay);
    }
    document.body.append(backdrop, drawer);
    list = drawer.querySelector('.ui-guide-list');
    search = drawer.querySelector('.ui-guide-search');
    search.addEventListener('input', () => render(search.value));
    drawer.querySelector('.ui-guide-close').addEventListener('click', close);
    render('');
  }

  function open(id) {
    build();
    search.value = '';
    render('');
    document.body.classList.add('ui-guide-open');
    if (id) {
      const row = list.querySelector('[data-id="' + id + '"]');
      if (row) {
        row.scrollIntoView({ block: 'center' });
        row.classList.remove('flash');
        void row.offsetWidth;
        row.classList.add('flash');
      }
    } else {
      list.scrollTop = 0;
      search.focus();
    }
  }

  function close() { document.body.classList.remove('ui-guide-open'); }

  document.addEventListener('click', (e) => {
    const trigger = e.target.closest && e.target.closest('[data-guide]');
    if (!trigger) return;
    e.preventDefault();
    e.stopPropagation();
    open(trigger.dataset.guide);
  }, true);

  document.addEventListener('keydown', (e) => {
    if (e.key === 'F1') {
      e.preventDefault();
      document.body.classList.contains('ui-guide-open') ? close() : open();
    } else if (e.key === 'Escape' && document.body.classList.contains('ui-guide-open')) {
      e.stopPropagation();
      close();
    }
  }, true);

  return { open, close };
})();
