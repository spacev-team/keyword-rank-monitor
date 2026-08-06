(function () {
  "use strict";

  var ENGINE_LABEL = {
    naver: "네이버",
    google: "구글",
    daum: "다음",
    playstore: "플레이스토어",
    appstore: "앱스토어"
  };
  var AREA_LABEL = { ad: "광고", organic: "오가닉", app: "앱" };
  var STATUS_LABEL = {
    ok: "정상",
    not_found: "권외",
    no_section: "광고영역 없음",
    blocked: "차단",
    parse_fail: "구조변경 의심",
    error: "수집오류"
  };
  var RANK_NONE_LABEL = {
    not_found: "권외",
    no_section: "광고없음",
    blocked: "차단",
    parse_fail: "확인필요",
    error: "오류"
  };
  var CARD_CELLS = [
    ["naver", "ad"], ["naver", "organic"],
    ["google", "ad"], ["google", "organic"],
    ["daum", "ad"], ["daum", "organic"],
    ["playstore", "app"], ["appstore", "app"]
  ];
  var BAD_STATUS = { blocked: 1, parse_fail: 1, error: 1 };
  var BRAND_GROUPS = { brand: 1, brand_ext: 1 };
  var CHART_COLORS = ["#6B4EFF", "#14934A", "#E07C1F", "#D93838", "#2A7DE1", "#8E44AD", "#0FA3A3", "#B8860B"];

  var state = {
    records: [],
    trends: { days: [], series: {} },
    filters: { engine: "", area: "", group: "", status: "", keyword: "" },
    sort: { key: null, dir: 1 },
    chart: null
  };

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function isBrand(group) { return BRAND_GROUPS[group] === 1; }

  /* 해당 키워드의 실제 검색결과 페이지 URL — 측정 방법론과 같은 대상(네이버·다음=PC 통합
     검색, 구글=ko/kr). 앱스토어는 공식 웹 검색결과 페이지가 없어 apple.com 통합검색으로 폴백. */
  function serpUrl(engine, keyword) {
    var q = encodeURIComponent(keyword);
    switch (engine) {
      case "naver": return "https://search.naver.com/search.naver?query=" + q;
      case "google": return "https://www.google.com/search?q=" + q + "&hl=ko&gl=kr";
      case "daum": return "https://search.daum.net/search?w=tot&q=" + q;
      case "playstore": return "https://play.google.com/store/search?q=" + q + "&c=apps&hl=ko&gl=KR";
      case "appstore": return "https://www.apple.com/kr/search/" + q + "?src=serp";
    }
    return null;
  }

  function goBtn(engine, keyword) {
    var url = serpUrl(engine, keyword);
    if (!url) return "–";
    return '<a class="go-btn" href="' + esc(url) + '" target="_blank" rel="noopener" ' +
      'title="' + (ENGINE_LABEL[engine] || esc(engine)) + ' 검색결과 새 탭에서 열기">보러가기</a>';
  }

  /* delta value: >0 up, <0 down, 0 flat, "new", "lost", null n/a */
  function deltaOf(r) {
    if (r.rank != null && r.prev_rank != null) return r.prev_rank - r.rank;
    if (r.rank != null && r.prev_rank == null) return "new";
    if (r.rank == null && r.prev_rank != null) return "lost";
    return null;
  }

  /* ---------- summary cards ---------- */
  function renderCards() {
    var html = CARD_CELLS.map(function (cell) {
      var engine = cell[0], area = cell[1];
      var rs = state.records.filter(function (r) { return r.engine === engine && r.area === area; });
      var blockedCount = rs.filter(function (r) { return r.status === "blocked"; }).length;
      var unavailable = rs.length === 0 || blockedCount / rs.length >= 0.5;
      var title = '<div class="card-title"><span>' + ENGINE_LABEL[engine] +
        '</span><span class="area-tag">' + AREA_LABEL[area] + "</span></div>";

      if (unavailable) {
        return '<div class="card card-blocked" data-engine="' + engine + '" data-area="' + area + '">' +
          title + '<div class="card-blocked-msg">수집 차단/미설정</div>' +
          '<div class="card-rows"><div class="card-row"><span>수집 레코드</span><b>' + rs.length + "개</b></div></div></div>";
      }

      function exposure(sub) {
        var shown = sub.filter(function (r) { return r.rank != null; }).length;
        return sub.length ? "<b>" + shown + "/" + sub.length + "</b>" : '<b class="none">–</b>';
      }
      var brand = rs.filter(function (r) { return isBrand(r.group); });
      var generic = rs.filter(function (r) { return !isBrand(r.group); });
      var top3 = rs.filter(function (r) { return r.rank != null && r.rank <= 3; }).length;
      var bad = rs.filter(function (r) { return BAD_STATUS[r.status] === 1; }).length;

      return '<div class="card" data-engine="' + engine + '" data-area="' + area + '">' + title +
        '<div class="card-rows">' +
        '<div class="card-row"><span>브랜드 노출</span>' + exposure(brand) + "</div>" +
        '<div class="card-row"><span>일반 노출</span>' + exposure(generic) + "</div>" +
        '<div class="card-row"><span>TOP3</span><b>' + top3 + "개</b></div>" +
        '<div class="card-row"><span>수집이상</span>' +
        (bad > 0 ? '<b class="bad-count">' + bad + "건</b>" : "<b>0건</b>") +
        "</div></div></div>";
    }).join("");
    $("summaryCards").innerHTML = html;
  }

  /* ---------- sparkline ---------- */
  function sparklineSVG(engine, area, keyword) {
    var key = engine + "|" + area + "|" + keyword;
    var series = state.trends.series[key];
    if (!series || !series.some(function (v) { return v != null; })) {
      return '<span class="spark-empty">–</span>';
    }
    var W = 90, H = 26, PAD = 3;
    var vals = series.filter(function (v) { return v != null; });
    var min = Math.min.apply(null, vals), max = Math.max.apply(null, vals);
    if (min === max) { min -= 1; max += 1; }
    var n = series.length;
    var x = function (i) { return n === 1 ? W / 2 : PAD + (i * (W - 2 * PAD)) / (n - 1); };
    /* rank axis inverted: rank 1 (best) at top */
    var y = function (v) { return PAD + ((v - min) * (H - 2 * PAD)) / (max - min); };

    var parts = [], seg = [];
    for (var i = 0; i < n; i++) {
      if (series[i] == null) {
        if (seg.length) { parts.push(seg); seg = []; }
      } else {
        seg.push([x(i), y(series[i])]);
      }
    }
    if (seg.length) parts.push(seg);

    var body = parts.map(function (p) {
      if (p.length === 1) {
        return '<circle cx="' + p[0][0].toFixed(1) + '" cy="' + p[0][1].toFixed(1) + '" r="2.2"/>';
      }
      var pts = p.map(function (q) { return q[0].toFixed(1) + "," + q[1].toFixed(1); }).join(" ");
      return '<polyline points="' + pts + '"/>';
    }).join("");
    return '<svg class="spark" width="' + W + '" height="' + H + '" viewBox="0 0 ' + W + " " + H + '" aria-hidden="true">' + body + "</svg>";
  }

  /* ---------- table ---------- */
  function filteredRecords() {
    var f = state.filters;
    var kw = f.keyword.trim().toLowerCase();
    return state.records.filter(function (r) {
      if (f.engine && r.engine !== f.engine) return false;
      if (f.area && r.area !== f.area) return false;
      if (f.group === "brand" && !isBrand(r.group)) return false;
      if (f.group === "generic" && isBrand(r.group)) return false;
      if (f.status && r.status !== f.status) return false;
      if (kw && r.keyword.toLowerCase().indexOf(kw) === -1) return false;
      return true;
    });
  }

  function sortRecords(rows) {
    var s = state.sort;
    if (!s.key) return rows;
    var dir = s.dir;
    rows.sort(function (a, b) {
      var va, vb;
      if (s.key === "keyword") {
        return dir * a.keyword.localeCompare(b.keyword, "ko");
      }
      if (s.key === "rank") {
        va = a.rank == null ? Infinity : a.rank; /* null(권외 등)은 항상 하단 */
        vb = b.rank == null ? Infinity : b.rank;
        if (va === Infinity && vb === Infinity) return 0;
        if (va === Infinity) return 1;
        if (vb === Infinity) return -1;
        return dir * (va - vb);
      }
      if (s.key === "delta") {
        va = deltaSortValue(a); vb = deltaSortValue(b);
        if (va === null && vb === null) return 0;
        if (va === null) return 1;
        if (vb === null) return -1;
        return dir * (vb - va); /* 기본(asc 클릭)에서 상승 큰 순 */
      }
      return 0;
    });
    return rows;
  }

  function deltaSortValue(r) {
    var d = deltaOf(r);
    if (d === "new") return 1000;
    if (d === "lost") return -1000;
    return d; /* number or null */
  }

  function deltaCell(r) {
    var d = deltaOf(r);
    if (d === "new") return '<span class="badge badge-new">신규</span>';
    if (d === "lost") return '<span class="badge badge-lost">이탈</span>';
    if (d === null) return '<span class="delta-flat">–</span>';
    if (d > 0) return '<span class="delta-up">▲ ' + d + "</span>";
    if (d < 0) return '<span class="delta-down">▼ ' + (-d) + "</span>";
    return '<span class="delta-flat">–</span>';
  }

  function rankCell(r) {
    if (r.rank != null) return '<span class="rank-val">' + r.rank + "위</span>";
    var label = RANK_NONE_LABEL[r.status] || "–";
    return '<span class="rank-none s-' + esc(r.status) + '">' + label + "</span>";
  }

  function renderTable() {
    var rows = sortRecords(filteredRecords());
    var body = $("tableBody");
    var html = "";
    for (var i = 0; i < rows.length; i++) {
      var r = rows[i];
      var groupBrand = isBrand(r.group);
      html += '<tr data-idx="' + r._idx + '">' +
        '<td class="kw">' + esc(r.keyword) + "</td>" +
        "<td><span class=\"badge " + (groupBrand ? "badge-group-brand\">브랜드" : "badge-group-generic\">일반") + "</span></td>" +
        "<td>" + (ENGINE_LABEL[r.engine] || esc(r.engine)) + "</td>" +
        "<td>" + (AREA_LABEL[r.area] || esc(r.area)) + "</td>" +
        '<td class="num">' + rankCell(r) + "</td>" +
        "<td>" + deltaCell(r) + "</td>" +
        "<td>" + esc(r.section || "–") + "</td>" +
        '<td class="num">' + (r.total != null ? r.total : "–") + "</td>" +
        '<td><span class="badge badge-s-' + esc(r.status) + '">' + (STATUS_LABEL[r.status] || esc(r.status)) + "</span></td>" +
        "<td>" + sparklineSVG(r.engine, r.area, r.keyword) + "</td>" +
        '<td class="go">' + goBtn(r.engine, r.keyword) + "</td>" +
        '<td class="time">' + esc(r.collected_at || "") + "</td>" +
        "</tr>";
    }
    body.innerHTML = html;
    $("emptyMsg").hidden = rows.length > 0;
    $("rowCount").textContent = rows.length + "건 / 전체 " + state.records.length + "건";
  }

  /* ---------- modal ---------- */
  function openModal(record) {
    var keyword = record.keyword;
    $("modalTitle").textContent = "\u201C" + keyword + "\u201D 순위 추이";

    var days = state.trends.days;
    var datasets = [];
    var ci = 0;
    Object.keys(state.trends.series).forEach(function (key) {
      var p = key.split("|");
      if (p.slice(2).join("|") !== keyword) return;
      var color = CHART_COLORS[ci % CHART_COLORS.length];
      ci++;
      datasets.push({
        label: (ENGINE_LABEL[p[0]] || p[0]) + " · " + (AREA_LABEL[p[1]] || p[1]),
        data: state.trends.series[key],
        borderColor: color,
        backgroundColor: color,
        spanGaps: false,
        tension: 0.2,
        pointRadius: days.length === 1 ? 4 : 2.5
      });
    });

    if (state.chart) { state.chart.destroy(); state.chart = null; }
    state.chart = new Chart($("modalChart"), {
      type: "line",
      data: { labels: days, datasets: datasets },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "nearest", intersect: false },
        scales: {
          y: {
            reverse: true,
            min: 1,
            ticks: { precision: 0, callback: function (v) { return v + "위"; } }
          }
        },
        plugins: { legend: { position: "bottom", labels: { boxWidth: 12, boxHeight: 12 } } }
      }
    });

    var links = state.records.filter(function (r) { return r.keyword === keyword && r.matched; });

    var goRow = '<div class="link-row"><span class="link-label">검색결과 바로가기</span><span class="go-links">' +
      ["naver", "google", "daum", "playstore", "appstore"].map(function (eng) {
        return '<a href="' + esc(serpUrl(eng, keyword)) + '" target="_blank" rel="noopener">' +
          (ENGINE_LABEL[eng] || eng) + "</a>";
      }).join(" · ") + "</span></div>";
    $("modalLinks").innerHTML = goRow + (links.length
      ? links.map(function (r) {
          var label = '<span class="link-label">' +
            (ENGINE_LABEL[r.engine] || esc(r.engine)) + " · " + (AREA_LABEL[r.area] || esc(r.area)) +
            (r.rank != null ? " · " + r.rank + "위" : "") + "</span>";
          var href = null;
          if (r.engine === "playstore") {
            href = "https://play.google.com/store/apps/details?id=" + encodeURIComponent(r.matched);
          } else if (r.engine === "appstore") {
            var appId = String(r.matched).replace(/^id/, "");
            href = /^\d+$/.test(appId) ? "https://apps.apple.com/kr/app/id" + appId : null;
          } else if (/^https?:\/\//.test(r.matched)) {
            href = r.matched;
          } else if (/^[\w.-]+\.[a-z]{2,}(\/|$)/i.test(r.matched)) {
            href = "https://" + r.matched;
          }
          var body = href
            ? '<a href="' + esc(href) + '" target="_blank" rel="noopener">' + esc(r.matched) + "</a>"
            : "<span>" + esc(r.matched) + "</span>";
          return '<div class="link-row">' + label + body + "</div>";
        }).join("")
      : '<div class="link-row"><span class="link-label">매칭된 URL 없음</span></div>');

    $("modal").hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeModal() {
    $("modal").hidden = true;
    document.body.style.overflow = "";
    if (state.chart) { state.chart.destroy(); state.chart = null; }
  }

  /* ---------- events ---------- */
  function bindEvents() {
    [["fEngine", "engine"], ["fArea", "area"], ["fGroup", "group"], ["fStatus", "status"]]
      .forEach(function (pair) {
        $(pair[0]).addEventListener("change", function (e) {
          state.filters[pair[1]] = e.target.value;
          renderTable();
        });
      });

    var searchTimer = null;
    $("fKeyword").addEventListener("input", function (e) {
      clearTimeout(searchTimer);
      var v = e.target.value;
      searchTimer = setTimeout(function () {
        state.filters.keyword = v;
        renderTable();
      }, 150);
    });

    document.querySelectorAll("th.sortable").forEach(function (th) {
      th.addEventListener("click", function () {
        var key = th.dataset.sort;
        if (state.sort.key === key) {
          state.sort.dir = -state.sort.dir;
        } else {
          state.sort.key = key;
          state.sort.dir = 1;
        }
        document.querySelectorAll("th.sortable").forEach(function (h) {
          h.classList.remove("sorted-asc", "sorted-desc");
        });
        th.classList.add(state.sort.dir === 1 ? "sorted-asc" : "sorted-desc");
        renderTable();
      });
    });

    $("tableBody").addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-idx]");
      if (e.target.closest("a")) return; /* 보러가기 링크 클릭은 모달을 띄우지 않는다 */
      if (!tr) return;
      var r = state.records[Number(tr.dataset.idx)];
      if (r) openModal(r);
    });

    $("modal").addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && !$("modal").hidden) closeModal();
    });
  }

  /* ---------- init ---------- */
  function init() {
    Promise.all([
      fetch("data/latest.json").then(function (r) {
        if (!r.ok) throw new Error("latest.json HTTP " + r.status);
        return r.json();
      }),
      fetch("data/trends.json").then(function (r) {
        if (!r.ok) throw new Error("trends.json HTTP " + r.status);
        return r.json();
      })
    ]).then(function (res) {
      var latest = res[0];
      state.records = (latest.records || []).map(function (r, i) { r._idx = i; return r; });
      state.trends = res[1] || { days: [], series: {} };
      $("generatedAt").textContent = "마지막 수집: " + (latest.generated_at || "–");
      renderCards();
      renderTable();
    }).catch(function (err) {
      $("summaryCards").innerHTML = '<div class="card card-blocked"><div class="card-blocked-msg">데이터를 불러오지 못했습니다: ' + esc(err.message) + "</div></div>";
    });
    bindEvents();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
