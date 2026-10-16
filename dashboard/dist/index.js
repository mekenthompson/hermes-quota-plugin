(function () {
  var sdk = window.__HERMES_PLUGIN_SDK__;
  var React = sdk.React;
  var h = React.createElement;
  var fetchJSON = sdk.fetchJSON;

  function remaining(used) {
    if (typeof used !== "number" || !isFinite(used)) return null;
    return Math.max(0, Math.min(100, Math.round(100 - used)));
  }

  function when(iso) {
    if (!iso) return "";
    var date = new Date(iso);
    if (isNaN(date.getTime())) return "";
    return date.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  }

  function Bar(props) {
    var left = remaining(props.used);
    var width = left == null ? 0 : left;
    return h("div", { style: { margin: "8px 0 14px" } },
      h("div", { style: { display: "flex", justifyContent: "space-between", fontSize: 13 } },
        h("span", null, props.label),
        h("span", null, left == null ? "n/a" : left + "% left")
      ),
      h("div", { style: { height: 8, borderRadius: 99, background: "var(--ui-stroke-secondary)", overflow: "hidden" } },
        h("div", { style: { width: width + "%", height: "100%", background: "var(--ui-accent)" } })
      ),
      props.reset ? h("div", { style: { fontSize: 12, color: "var(--ui-text-tertiary)", marginTop: 4 } }, "Resets " + when(props.reset)) : null
    );
  }

  function Card(props) {
    var rec = props.record;
    var title = rec.account_label && rec.account_label !== rec.label ? rec.label + " · " + rec.account_label : rec.label;
    return h("section", { style: { padding: "12px 0", borderBottom: "1px solid var(--ui-stroke-secondary)" } },
      h("div", { style: { display: "flex", gap: 8, alignItems: "baseline" } },
        h("strong", null, title),
        rec.plan ? h("span", { style: { color: "var(--ui-text-tertiary)", fontSize: 12 } }, rec.plan) : null
      ),
      rec.unavailable_reason
        ? h("div", { style: { color: "var(--ui-text-tertiary)", fontSize: 13, marginTop: 6 } }, "unavailable (" + rec.unavailable_reason + ")")
        : (rec.windows || []).map(function (w, i) {
            return h(Bar, { key: w.label + i, label: w.label, used: w.used_percent, reset: w.reset_at });
          }),
      (rec.details || []).map(function (line, i) {
        return h("div", { key: i, style: { fontSize: 12, color: "var(--ui-text-secondary)" } }, line);
      }),
      (rec.accounts || []).map(function (account, i) {
        return h(Card, { key: account.label + i, record: account });
      })
    );
  }

  function QuotaPage() {
    var state = React.useState({ loading: true, error: "", data: null });
    var snap = state[0];
    var setSnap = state[1];
    var historyState = React.useState(null);
    var hist = historyState[0];
    var setHistory = historyState[1];

    var forecastState = React.useState(null);
    var forecast = forecastState[0];
    var setForecast = forecastState[1];

    function loadHistory() {
      fetchJSON("/api/plugins/quota/history").then(setHistory).catch(function () { setHistory({ error: true }); });
      fetchJSON("/api/plugins/quota/forecast").then(setForecast).catch(function () { setForecast({ error: true }); });
    }

    function load(refresh) {
      setSnap(function (prev) { return { loading: true, error: "", data: prev.data }; });
      var url = refresh ? "/api/plugins/quota/refresh" : "/api/plugins/quota/status";
      fetchJSON(url, refresh ? { method: "POST" } : undefined).then(function (data) {
        setSnap({ loading: false, error: "", data: data });
        loadHistory();
      }).catch(function (err) {
        setSnap(function (prev) { return { loading: false, error: String(err && err.message || err), data: prev.data }; });
      });
    }

    React.useEffect(function () {
      load(false);
    }, []);

    var providers = snap.data && snap.data.providers ? snap.data.providers : {};
    var names = Object.keys(providers).sort(function (a, b) {
      var rank = { grok: 0, anthropic: 1, "openai-codex": 2, openrouter: 3 };
      return (rank[a] == null ? 9 : rank[a]) - (rank[b] == null ? 9 : rank[b]) || a.localeCompare(b);
    });

    return h("div", { style: { padding: 20, maxWidth: 760, color: "var(--ui-text-secondary)" } },
      h("div", { style: { display: "flex", justifyContent: "space-between", alignItems: "center" } },
        h("h1", { style: { fontSize: 20, margin: 0 } }, "Quota"),
        h("button", { type: "button", onClick: function () { load(true); }, disabled: snap.loading }, snap.loading ? "Refreshing" : "Refresh")
      ),
      h("p", { style: { fontSize: 13, color: "var(--ui-text-tertiary)" } },
        "Bars are remaining subscription allowance. OpenRouter is a prepaid wallet, not a subscription. Tailnet only."
      ),
      snap.error ? h("p", null, snap.error) : null,
      h("h2", { style: { fontSize: 16, marginTop: 20 } }, "Will the quota last until reset?"),
      h("p", { style: { fontSize: 12 } }, "Each shared account already includes all clients using it. Accounts and windows are never summed. Estimates assume the last 24 hours of continuous observed pace continues; they are not a vendor guarantee."),
      !forecast ? h("p", null, "Loading forecast") : forecast.error ? h("p", null, "Forecast unavailable") :
        (forecast.forecasts || []).map(function (f, i) {
          var estimate = f.projected_used_percent_at_reset;
          return h("section", { key: f.provider + f.account + f.window + i, style: { marginBottom: 14 } },
            h("strong", null, f.label + " · " + f.window),
            h("div", null, f.used_percent == null ? "Usage unavailable" : f.used_percent.toFixed(1) + "% used · " + f.remaining_percent.toFixed(1) + "% remaining at last sample"),
            h("div", null, f.status),
            f.reset_at ? h("div", null, "Resets " + when(f.reset_at)) : null,
            estimate == null ? null : h("div", null, "Projected demand: " + estimate.toFixed(1) + "% of quota by reset · " + f.rate_percentage_points_per_hour.toFixed(2) + " percentage points/hour"),
            f.estimated_exhaustion_at ? h("div", null, "Estimated exhaustion at observed pace: " + when(f.estimated_exhaustion_at)) : null,
            h("div", { style: { fontSize: 12 } }, "Observed " + when(f.fetched_at) + " · " + f.sample_count + " continuous samples")
          );
        }),
      h("h2", { style: { fontSize: 16, marginTop: 20 } }, "Observed daily quota changes (UTC)"),
      h("p", { style: { fontSize: 12 } }, "Sampled percentage-point changes, not vendor daily usage totals. Gaps, unknown identities and resets are not joined."),
      !hist ? h("p", null, "Loading history") : hist.error ? h("p", null, "History unavailable") :
        h("div", null,
          h("p", null, String((hist.samples || []).length) + " stored observations. Scheduled collection is configured separately; this page does not poll providers automatically."),
          (hist.daily_changes || []).length ? hist.daily_changes.map(function (row, i) {
            return h("div", { key: i, style: { fontSize: 13, marginBottom: 6 } },
              row.day_utc + " · " + row.provider + " · " + row.account.slice(0, 8) + " · " + row.window + ": " + row.observed_percentage_points.toFixed(1) + " percentage points observed");
          }) : h("p", null, "Insufficient comparable samples for a daily trend.")
        ),
      names.map(function (name) { return h(Card, { key: name, record: providers[name] }); })
    );
  }

  window.__HERMES_PLUGINS__.register("quota", QuotaPage);
})();
