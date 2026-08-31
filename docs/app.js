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
    not_found: "미노출",
    no_section: "광고영역 없음",
    blocked: "차단",
    parse_fail: "구조변경 의심",
    error: "수집오류"
  };
  /* 구글 광고는 측정 제외(유료 SerpAPI 없이 측정 불가 — 사용자 확정 2026-08-07) */
  var CARD_CELLS = [
    ["naver", "ad"], ["naver", "organic"],
    ["google", "organic"], ["daum", "ad"],
    ["daum", "organic"], ["playstore", "app"],
    ["appstore", "app"]
  ];
  var BAD_STATUS = { blocked: 1, parse_fail: 1, error: 1 };
  var BRAND_GROUPS = { brand: 1, brand_ext: 1 };
  var CHART_COLORS = ["#6B4EFF", "#14934A", "#E07C1F", "#D93838", "#2A7DE1", "#8E44AD", "#0FA3A3", "#B8860B"];

  /* ── 매트릭스 채널(열) 정의 — 목업 순서 그대로 ──
     excluded=true 는 상시 측정 제외(구글 광고). key = engine|area 로 records 조인. */
  var CHANNELS = [
    { key: "naver|ad", engine: "naver", area: "ad", label: "네이버 광고", grp: "ad" },
    { key: "naver|organic", engine: "naver", area: "organic", label: "네이버 오가닉", grp: "organic" },
    { key: "google|organic", engine: "google", area: "organic", label: "구글 SEO", grp: "organic" },
    { key: "daum|ad", engine: "daum", area: "ad", label: "다음 광고", grp: "ad" },
    { key: "daum|organic", engine: "daum", area: "organic", label: "다음 오가닉", grp: "organic" },
    { key: "appstore|app", engine: "appstore", area: "app", label: "App Store 오가닉", grp: "app" },
    { key: "playstore|app", engine: "playstore", area: "app", label: "Google Play 오가닉", grp: "app" }
  ];
  var CHANNEL_BY_KEY = {};
  CHANNELS.forEach(function (c) { CHANNEL_BY_KEY[c.key] = c; });

  /* ── 종합(가중 평균 순위) 정의 — 측정된 채널만으로 가중치 재정규화(사용자 확정) ── */
  var COMPOSITES = [
    { id: "ad", label: "광고 종합", note: "네이버 0.8 · 다음 0.2",
      weights: [["naver|ad", 0.8], ["daum|ad", 0.2]] },
    { id: "organic", label: "오가닉 종합", note: "네이버 0.4 · 구글 0.5 · 다음 0.1",
      weights: [["naver|organic", 0.4], ["google|organic", 0.5], ["daum|organic", 0.1]] },
    { id: "app", label: "앱 종합", note: "플레이스토어 0.5 · 앱스토어 0.5",
      weights: [["playstore|app", 0.5], ["appstore|app", 0.5]] }
  ];

  /* ── 구분(카테고리) 메타 — 사용자 정의 3분류(2026-08-28) ──
     Brand=검색 방어(1위 필수) · Category=신규 확보(TOP3) · Competitor=대안 탐색(TOP10).
     중요도 별점은 이 목표 티어를 반영(★★★/★★/★) — 조정하려면 stars 만 바꾸면 된다. */
  var CATEGORIES = {
    brand: { order: 0, label: "Brand", cls: "cat-brand", stars: 3, goal: "검색 결과 방어 · 1위 필수" },
    category: { order: 1, label: "Category", cls: "cat-category", stars: 2, goal: "신규 고객 확보 · TOP3 확대" },
    competitor: { order: 2, label: "Competitor / Alternative", cls: "cat-competitor", stars: 1, goal: "대안 탐색 고객 확보 · TOP10 진입" }
  };

  var state = {
    records: [],
    trends: { days: [], series: {} },
    filters: { keyword: "", category: "" },
    sort: { key: "category", dir: 1 },
    view: "status",
    chart: null,
    generatedAt: ""
  };

  var $ = function (id) { return document.getElementById(id); };

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function isBrand(group) { return BRAND_GROUPS[group] === 1; }

  /* group → 매트릭스 구분(brand|category|competitor) */
  function categoryOf(group) {
    if (isBrand(group)) return "brand";
    if (group === "competitor") return "competitor";
    return "category";
  }

  /* 해당 키워드의 실제 검색결과 페이지 URL — 측정 방법론과 같은 대상. */
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

  /* delta value: >0 up, <0 down, 0 flat, "new", "lost", null n/a */
  function deltaOf(r) {
    if (r.rank != null && r.prev_rank != null) return r.prev_rank - r.rank;
    if (r.rank != null && r.prev_rank == null) return "new";
    if (r.rank == null && r.prev_rank != null) return "lost";
    return null;
  }

  /* blocked/error/parse_fail 은 '측정'이 아니므로 변동으로 치지 않는다. */
  function changeKind(r) {
    if (BAD_STATUS[r.status] === 1) return null;
    var d = deltaOf(r);
    if (d === "lost" || (typeof d === "number" && d < 0)) return "down";
    if (d === "new" || (typeof d === "number" && d > 0)) return "up";
    return null;
  }

  function changeMagnitude(r) {
    var d = deltaOf(r);
    if (d === "new" || d === "lost") return 1000;
    return typeof d === "number" ? Math.abs(d) : 0;
  }

  /* rank → 색 등급: 1~3 green / 4~10 yellow / 11+ red */
  function rankClass(rank) {
    if (rank == null) return "r";
    if (rank <= 3) return "g";
    if (rank <= 10) return "y";
    return "r";
  }

  function dailySv(sv) { return sv == null ? null : Math.round(sv / 30); }
  function fmt(n) { return n == null ? "–" : n.toLocaleString(); }

  /* ---------- summary cards ---------- */
  var PINNED_KEYWORDS = ["삼삼엠투", "33M2", "단기임대"];

  function countDelta(now, prev) {
    var d = now - prev;
    if (d > 0) return '<span class="delta-up">▲' + d + "</span>";
    if (d < 0) return '<span class="delta-down">▼' + (-d) + "</span>";
    return "";
  }

  function staleTag(rs) {
    if (!rs.length || !state.generatedAt) return "";
    var newest = "";
    for (var i = 0; i < rs.length; i++) {
      if (rs[i].collected_at > newest) newest = rs[i].collected_at;
    }
    var lagMs = new Date(state.generatedAt.replace(" ", "T")) - new Date(newest.replace(" ", "T"));
    if (!newest || isNaN(lagMs) || lagMs < 6 * 3600 * 1000) return "";
    var d = newest.slice(5, 16).replace("-", "/");
    return '<span class="stale-tag" title="이후 수집이 차단되어 마지막 정상 수집 데이터를 표시 중 — 다음 정상 수집 시 자동 갱신">' +
      d + " 데이터</span>";
  }

  function rankCell(r) {
    var suffix = r.total > 0 ? ' <span class="rank-total">/ ' + r.total + "</span>" : "";
    if (r.rank != null) return '<span class="rank-val">' + r.rank + "위</span>" + suffix;
    var label = { not_found: "미노출", no_section: "광고없음", blocked: "차단", parse_fail: "확인필요", error: "오류" }[r.status] || "–";
    return '<span class="rank-none s-' + esc(r.status) + '">' + label + "</span>" +
      (r.status === "not_found" ? suffix : "");
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

  function renderCards() {
    var html = CARD_CELLS.map(function (cell) {
      var engine = cell[0], area = cell[1];
      var rs = state.records.filter(function (r) { return r.engine === engine && r.area === area; });
      var blockedCount = rs.filter(function (r) { return r.status === "blocked"; }).length;
      var unavailable = rs.length === 0 || blockedCount / rs.length >= 0.5;
      var title = '<div class="card-title"><span>' + ENGINE_LABEL[engine] +
        '</span>' + staleTag(rs) + '<span class="area-tag">' + AREA_LABEL[area] + "</span></div>";

      if (unavailable) {
        return '<div class="card card-blocked" data-engine="' + engine + '" data-area="' + area + '">' +
          title + '<div class="card-blocked-msg">수집 차단/미설정</div>' +
          '<div class="card-rows"><div class="card-row"><span>수집 레코드</span><b>' + rs.length + "개</b></div></div></div>";
      }

      var hasPrev = rs.some(function (r) { return r.prev_rank != null; });

      var pinned = PINNED_KEYWORDS.map(function (kw) {
        var r = null;
        for (var i = 0; i < rs.length; i++) {
          if (rs[i].keyword === kw) { r = rs[i]; break; }
        }
        if (!r) {
          return '<div class="card-kw-row is-excluded" title="이 엔진에서는 측정하지 않는 키워드(무료 API 쿼터로 브랜드만 수집)">' +
            '<span class="card-kw">' + esc(kw) + "</span>" +
            '<span class="card-val"><span class="delta-flat">측정 제외</span></span></div>';
        }
        return '<div class="card-kw-row" data-kw="' + esc(kw) + '" title="순위 추이 보기">' +
          '<span class="card-kw">' + esc(kw) + "</span>" +
          '<span class="card-val">' + rankCell(r) + (hasPrev ? deltaCell(r) : "") + "</span></div>";
      }).join("");

      function exposure(sub) {
        if (!sub.length) return '<span class="card-val" title="무료 API 쿼터 때문에 구글은 브랜드 키워드만 수집"><span class="delta-flat">측정 제외</span></span>';
        var now = sub.filter(function (r) { return r.rank != null; }).length;
        var prev = sub.filter(function (r) { return r.prev_rank != null; }).length;
        return '<span class="card-val"><b>' + now + "/" + sub.length + "</b>" +
          (hasPrev ? countDelta(now, prev) : "") + "</span>";
      }
      var brand = rs.filter(function (r) { return isBrand(r.group); });
      var generic = rs.filter(function (r) { return !isBrand(r.group); });
      var top3 = rs.filter(function (r) { return r.rank != null && r.rank <= 3; }).length;
      var top3prev = rs.filter(function (r) { return r.prev_rank != null && r.prev_rank <= 3; }).length;
      var bad = rs.filter(function (r) { return BAD_STATUS[r.status] === 1; }).length;

      var dropLine = "";
      if (hasPrev) {
        var worst = null;
        var drops = rs.filter(function (r) {
          var d = deltaOf(r);
          if (typeof d === "number" && d < 0) {
            if (worst === null || d < deltaOf(worst)) worst = r;
            return true;
          }
          return d === "lost";
        });
        if (drops.length) {
          var head = worst
            ? esc(worst.keyword) + " " + worst.prev_rank + "→" + worst.rank + "위"
            : "노출 이탈 " + drops.length + "건";
          var extra = worst && drops.length > 1 ? " 외 " + (drops.length - 1) + "건" : "";
          dropLine = '<div class="card-drop" title="전회 대비 순위 하락·노출 이탈">▼ ' + head + extra + "</div>";
        }
      }

      return '<div class="card" data-engine="' + engine + '" data-area="' + area + '">' + title +
        (pinned ? '<div class="card-kws">' + pinned + "</div>" : "") +
        '<div class="card-rows">' +
        '<div class="card-row"><span class="kw-group-link" data-group="brand" title="키워드 목록 보기">브랜드 노출</span>' + exposure(brand) + "</div>" +
        '<div class="card-row"><span class="kw-group-link" data-group="generic" title="키워드 목록 보기">일반 노출</span>' + exposure(generic) + "</div>" +
        '<div class="card-row"><span class="kw-group-link top3-link" title="TOP3 설명·키워드 보기">TOP3</span><span class="card-val"><b>' + top3 + "개</b>" +
        (hasPrev ? countDelta(top3, top3prev) : "") + "</span></div>" +
        '<div class="card-row"><span class="top3-plain">수집이상</span>' +
        (bad > 0 ? '<b class="bad-count">' + bad + "건</b>" : "<b>0건</b>") +
        "</div></div>" + dropLine + "</div>";
    }).join("");
    $("summaryCards").innerHTML = html;
  }

  /* ---------- change briefing ---------- */
  var BRIEF_LIMIT = 8;

  function briefMove(r) {
    var d = deltaOf(r);
    if (d === "lost") return '<span class="badge badge-lost">이탈</span>';
    if (d === "new") return '<span class="badge badge-new">신규</span>';
    var cls = d < 0 ? "delta-down" : "delta-up";
    return '<span class="' + cls + '">' + r.prev_rank + "→" + r.rank + "위</span>";
  }

  function briefPanel(kind, rows) {
    var head = kind === "down" ? "🔻 하락·이탈" : "🔺 상승·신규";
    var html = '<div class="brief-panel brief-' + kind + '">' +
      '<div class="brief-head">' + head + " <b>" + rows.length + "건</b></div>";
    if (!rows.length) {
      return html + '<div class="brief-none">변동 없음</div></div>';
    }
    html += '<div class="brief-list">' + rows.slice(0, BRIEF_LIMIT).map(function (r) {
      var sv = dailySv(r.sv);
      return '<button type="button" class="brief-row" data-idx="' + r._idx + '" title="순위 추이 보기">' +
        '<span class="brief-kw">' + esc(r.keyword) + "</span>" +
        '<span class="brief-meta">' + (ENGINE_LABEL[r.engine] || esc(r.engine)) + "/" + (AREA_LABEL[r.area] || esc(r.area)) + "</span>" +
        '<span class="brief-move">' + briefMove(r) + "</span>" +
        '<span class="brief-sv" title="네이버 검색량(일평균)">검색량 ' + (sv == null ? "–" : fmt(sv) + "/일") + "</span></button>";
    }).join("") + "</div>";
    return html + "</div>";
  }

  function renderBriefing() {
    var box = $("briefing");
    var hasPrev = state.records.some(function (r) { return r.prev_rank != null; });
    if (!hasPrev) { box.hidden = true; return; }

    var byKind = { down: [], up: [] };
    state.records.forEach(function (r) {
      var k = changeKind(r);
      if (k) byKind[k].push(r);
    });
    var bySvThenMagnitude = function (a, b) {
      return (b.sv || 0) - (a.sv || 0) || changeMagnitude(b) - changeMagnitude(a);
    };
    byKind.down.sort(bySvThenMagnitude);
    byKind.up.sort(bySvThenMagnitude);

    box.innerHTML = briefPanel("down", byKind.down) + briefPanel("up", byKind.up);
    box.hidden = false;
  }

  /* ---------- matrix ---------- */
  /* records → 키워드별 { keyword, group, category, sv, channels{key:rec} } (검색량 desc, 구분 순) */
  function buildRows() {
    var byKw = {};
    state.records.forEach(function (r) {
      var m = byKw[r.keyword];
      if (!m) {
        m = byKw[r.keyword] = {
          keyword: r.keyword, group: r.group, category: categoryOf(r.group),
          sv: r.sv == null ? null : r.sv, channels: {}
        };
      }
      if (m.sv == null && r.sv != null) m.sv = r.sv;
      m.channels[r.engine + "|" + r.area] = r;
    });
    var rows = Object.keys(byKw).map(function (k) { return byKw[k]; });
    rows.sort(rowComparator);
    return rows;
  }

  /* 정렬 키 → 숫자값(작을수록 상위). null = 데이터 없음(항상 하단). */
  function sortNum(row, key) {
    if (key === "sv") return row.sv == null ? null : row.sv;
    if (key === "imp") return CATEGORIES[row.category].stars;
    if (key.indexOf("cmp_") === 0) {
      var comp = null;
      for (var i = 0; i < COMPOSITES.length; i++) if (COMPOSITES[i].id === key.slice(4)) comp = COMPOSITES[i];
      if (!comp) return null;
      var c = composite(row.channels, comp);
      return c ? c.value : null;
    }
    // 채널 key(engine|area) → 순위(측정제외·미노출·수집이상 = null)
    var rec = row.channels[key];
    if (!rec || rec.rank == null) return null;
    return rec.rank;
  }

  function rowComparator(a, b) {
    var s = state.sort, k = s.key, dir = s.dir;
    if (k === "category") {
      // 구분별 그룹 보기 — 구분(dir) → 검색량 desc → 키워드
      var ca = CATEGORIES[a.category].order, cb = CATEGORIES[b.category].order;
      if (ca !== cb) return dir * (ca - cb);
      var sa = a.sv == null ? -1 : a.sv, sb = b.sv == null ? -1 : b.sv;
      if (sa !== sb) return sb - sa;
      return a.keyword.localeCompare(b.keyword, "ko");
    }
    if (k === "keyword") return dir * a.keyword.localeCompare(b.keyword, "ko");
    var va = sortNum(a, k), vb = sortNum(b, k);
    var na = va == null, nb = vb == null;
    if (na && nb) return a.keyword.localeCompare(b.keyword, "ko");
    if (na) return 1;   // 데이터 없음은 방향과 무관하게 항상 하단
    if (nb) return -1;
    if (va !== vb) return dir * (va - vb);
    return a.keyword.localeCompare(b.keyword, "ko");
  }

  function applyFilters(rows) {
    var f = state.filters;
    var kw = f.keyword.trim().toLowerCase();
    return rows.filter(function (row) {
      if (f.category && row.category !== f.category) return false;
      if (kw && row.keyword.toLowerCase().indexOf(kw) === -1) return false;
      return true;
    });
  }

  /* 종합 = 측정된 채널만으로 가중 평균 순위(가중치 재정규화).
     ok=순위, not_found=미노출→total+1 페널티(측정됨). 측정제외·수집이상은 제외. */
  function composite(chMap, comp) {
    var wsum = 0, acc = 0, parts = [];
    comp.weights.forEach(function (w) {
      var key = w[0], weight = w[1];
      var rec = chMap[key];
      if (!rec) return;                 // 측정 제외 / 미수집
      if (rec.status !== "ok" && rec.status !== "not_found") return; // 수집이상 제외
      var eff = rec.rank != null ? rec.rank : (rec.total > 0 ? rec.total + 1 : 11);
      wsum += weight; acc += weight * eff;
      parts.push(CHANNEL_BY_KEY[key].label + " " + (rec.rank != null ? rec.rank + "위" : "미노출"));
    });
    if (wsum === 0) return null;
    return { value: acc / wsum, parts: parts };
  }

  function dot(cls) { return '<span class="dot ' + cls + '"></span>'; }

  /* 장악 현황 셀 */
  function statusCell(rec, ch) {
    if (!rec) {
      var t = ch.excluded ? "구글 광고는 측정 제외(유료 SerpAPI 필요)" : "측정 제외 / 미수집";
      return '<td class="mx na" title="' + t + '">' + dot("na") + "</td>";
    }
    if (BAD_STATUS[rec.status] === 1) {
      return '<td class="mx na" title="수집이상: ' + esc(STATUS_LABEL[rec.status] || rec.status) + '">' +
        dot("na") + '<span class="mx-sub">수집이상</span></td>';
    }
    if (rec.rank != null) {
      var suf = rec.total > 0 ? " /" + rec.total : "";
      return '<td class="mx" title="' + rec.rank + "위" + (rec.total > 0 ? " (" + rec.total + "개 중)" : "") + '">' +
        dot(rankClass(rec.rank)) + '<span class="mx-rank">' + rec.rank + '</span><span class="mx-tot">' + suf + "</span></td>";
    }
    // not_found → 미노출(빨강)
    return '<td class="mx" title="미노출' + (rec.total > 0 ? " (" + rec.total + "개 스캔)" : "") + '">' +
      dot("r") + '<span class="mx-none">미노출</span></td>';
  }

  function trendAt(engine, area, keyword, backDays) {
    var days = state.trends.days;
    if (!days || !days.length) return null;
    var series = state.trends.series[engine + "|" + area + "|" + keyword];
    if (!series) return null;
    var idx = days.length - 1 - backDays;
    if (idx < 0 || idx >= series.length) return null;
    var v = series[idx];
    return v == null ? null : v;
  }

  /* 순위 Trend 셀 — 전일 대비(가시) + 전주·4주전(툴팁) */
  function trendCell(rec, ch, keyword) {
    if (!rec) {
      var t = ch.excluded ? "구글 광고는 측정 제외" : "측정 제외 / 미수집";
      return '<td class="mx na" title="' + t + '">' + dot("na") + "</td>";
    }
    if (BAD_STATUS[rec.status] === 1) {
      return '<td class="mx na" title="수집이상">' + dot("na") + '<span class="mx-sub">수집이상</span></td>';
    }
    var cur = rec.rank, prev = rec.prev_rank;
    var wk = trendAt(ch.engine, ch.area, keyword, 7);
    var mo = trendAt(ch.engine, ch.area, keyword, 28);
    var tip = "전일 " + (prev == null ? "–" : prev + "위") +
      " · 전주 " + (wk == null ? "–" : wk + "위") +
      " · 4주전 " + (mo == null ? "–" : mo + "위");
    if (cur == null && prev == null) {
      return '<td class="mx" title="미노출 · ' + tip + '">' + dot("r") + '<span class="mx-none">미노출</span></td>';
    }
    if (cur == null) {
      return '<td class="mx" title="노출 이탈 · ' + tip + '">' + dot("r") + '<span class="badge badge-lost">이탈</span></td>';
    }
    var body;
    if (prev == null) {
      body = '<span class="mx-rank">' + cur + '</span> <span class="badge badge-new">신규</span>';
    } else if (prev === cur) {
      body = '<span class="mx-trend">' + prev + "→" + cur + "</span>";
    } else {
      var arrow = cur < prev ? '<span class="delta-up">▲' + (prev - cur) + "</span>"
        : '<span class="delta-down">▼' + (cur - prev) + "</span>";
      body = '<span class="mx-trend">' + prev + "→" + cur + "</span> " + arrow;
    }
    return '<td class="mx" title="' + tip + '">' + dot(rankClass(cur)) + body + "</td>";
  }

  function compositeCell(chMap, comp) {
    var c = composite(chMap, comp);
    if (!c) return '<td class="mx cmp na" title="측정된 채널 없음">' + dot("na") + "</td>";
    var r = Math.round(c.value);
    var tip = "가중 평균 " + c.value.toFixed(1) + "위 · " + c.parts.join(" · ");
    return '<td class="mx cmp" title="' + esc(tip) + '">' + dot(rankClass(r)) +
      '<span class="cmp-val">' + c.value.toFixed(1) + "</span></td>";
  }

  function sortArrow(key) {
    if (state.sort.key !== key) return ' <span class="sort-ind dim">↕</span>';
    return state.sort.dir === 1 ? ' <span class="sort-ind">▲</span>' : ' <span class="sort-ind">▼</span>';
  }

  function thCell(key, cls, label, title) {
    var active = state.sort.key === key;
    return '<th class="mx-h sortable ' + cls + (active ? " is-sorted" : "") + '" data-sort="' + key + '"' +
      (title ? ' title="' + esc(title) + '"' : "") + ">" + esc(label) + sortArrow(key) + "</th>";
  }

  function renderHead() {
    var chHtml = CHANNELS.map(function (c) {
      var active = state.sort.key === c.key;
      return '<th class="mx-h sortable ch-' + c.grp + (c.excluded ? " excluded" : "") + (active ? " is-sorted" : "") +
        '" data-sort="' + c.key + '">' + esc(c.label) + sortArrow(c.key) + "</th>";
    }).join("");
    var cmpHtml = COMPOSITES.map(function (c) {
      var key = "cmp_" + c.id, active = state.sort.key === key;
      return '<th class="mx-h sortable cmp-h' + (active ? " is-sorted" : "") + '" data-sort="' + key +
        '" title="' + esc(c.note) + '"><span class="cmp-h-t">' + esc(c.label) + sortArrow(key) +
        '</span><span class="cmp-h-n">' + esc(c.note) + "</span></th>";
    }).join("");
    $("matrixHead").innerHTML =
      "<tr>" +
      thCell("category", "col-cat", "구분", "구분별 그룹 보기 · 클릭 시 순서 전환") +
      thCell("keyword", "col-kw", "키워드", "가나다순 정렬") +
      thCell("imp", "col-imp", "중요도", "구분별 목표 티어(★★★ 방어 / ★★ 확대 / ★ 진입)") +
      thCell("sv", "col-sv num", "네이버 검색량/일", "네이버 키워드도구 최근 30일 검색수 ÷ 30 = 일평균") +
      chHtml + cmpHtml + "</tr>";
  }

  /* 첫 클릭 방향: 검색량·중요도는 큰 값 우선(내림), 순위·키워드·구분은 오름 */
  function defaultDir(key) { return (key === "sv" || key === "imp") ? -1 : 1; }

  function stars(cat) {
    var n = CATEGORIES[cat].stars;
    var s = "";
    for (var i = 0; i < 3; i++) s += i < n ? "★" : "";
    return s;
  }

  function renderMatrix() {
    renderHead();
    var rows = applyFilters(buildRows());
    var body = $("matrixBody");
    var trend = state.view === "trend";
    var grouped = state.sort.key === "category";
    var html = "";
    var lastCat = null;
    for (var i = 0; i < rows.length; i++) {
      var row = rows[i];
      var cat = CATEGORIES[row.category];
      if (grouped && row.category !== lastCat) {
        html += '<tr class="cat-sep ' + cat.cls + '"><td colspan="' + (4 + CHANNELS.length + COMPOSITES.length) +
          '"><span class="cat-sep-label">' + esc(cat.label) + "</span>" +
          '<span class="cat-sep-goal">' + esc(cat.goal) + "</span></td></tr>";
        lastCat = row.category;
      }
      var svd = dailySv(row.sv);
      var cells = "";
      CHANNELS.forEach(function (ch) {
        var rec = row.channels[ch.key];
        cells += trend ? trendCell(rec, ch, row.keyword) : statusCell(rec, ch);
      });
      COMPOSITES.forEach(function (comp) { cells += compositeCell(row.channels, comp); });

      html += '<tr data-kw="' + esc(row.keyword) + '" title="순위 추이 보기">' +
        '<td class="col-cat"><span class="cat-badge ' + cat.cls + '">' + esc(cat.label) + "</span></td>" +
        '<td class="col-kw">' + esc(row.keyword) + "</td>" +
        '<td class="col-imp" title="' + esc(cat.goal) + '">' + stars(row.category) + "</td>" +
        '<td class="col-sv num" title="' + (row.sv == null ? "검색량 미확인" : "최근 30일 총 " + fmt(row.sv) + "회") + '">' +
        (svd == null ? "–" : fmt(svd)) + "</td>" +
        cells + "</tr>";
    }
    body.innerHTML = html;
    $("emptyMsg").hidden = rows.length > 0;

    var total = buildRows().length;
    var label = rows.length === total
      ? total.toLocaleString() + "개 키워드"
      : rows.length.toLocaleString() + " / " + total.toLocaleString() + "개 키워드";
    $("rowCount").textContent = label;
  }

  /* ---------- modal (순위 추이 + 링크) ---------- */
  function openModalByKeyword(keyword) {
    var rec = null;
    for (var i = 0; i < state.records.length; i++) {
      if (state.records[i].keyword === keyword) { rec = state.records[i]; break; }
    }
    if (rec) openModal(rec);
  }

  function openModal(record) {
    var keyword = record.keyword;
    $("modalTitle").textContent = "\u201C" + keyword + "\u201D 순위 추이";

    var days = state.trends.days;
    var datasets = [];
    var ci = 0;
    Object.keys(state.trends.series).forEach(function (key) {
      var p = key.split("|");
      if (p.slice(2).join("|") !== keyword) return;
      var series = state.trends.series[key];
      if (!series.some(function (v) { return v != null; })) return;
      var color = CHART_COLORS[ci % CHART_COLORS.length];
      ci++;
      datasets.push({
        label: (ENGINE_LABEL[p[0]] || p[0]) + " · " + (AREA_LABEL[p[1]] || p[1]),
        data: series,
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

  /* ---------- keyword group modal ---------- */
  var GROUP_INFO = {
    brand: {
      title: "브랜드 키워드",
      desc: "\u2018삼삼엠투\u2019, \u201833m2\u2019 가 포함된 자사 브랜드 키워드(수식어 확장 조합 포함)."
    },
    generic: {
      title: "일반 키워드",
      desc: "네이버 브랜드 검색에서 전환이 발생하고 상위 비용을 차지하는 키워드."
    }
  };

  function openKwModal(group) {
    var seen = {};
    var kws = [];
    state.records.forEach(function (r) {
      if ((group === "brand") !== isBrand(r.group)) return;
      if (seen[r.keyword]) return;
      seen[r.keyword] = 1;
      kws.push(r.keyword);
    });
    $("kwModalTitle").textContent = GROUP_INFO[group].title + " · " + kws.length + "개";
    $("kwModalDesc").textContent = GROUP_INFO[group].desc;
    $("kwModalChips").innerHTML = kws.map(function (k) {
      return '<button type="button" class="kw-chip ' + group + '" data-kw="' + esc(k) + '">' + esc(k) + "</button>";
    }).join("");
    $("kwModal").hidden = false;
    document.body.style.overflow = "hidden";
  }

  function closeKwModal() {
    $("kwModal").hidden = true;
    document.body.style.overflow = "";
  }

  function openTop3Modal(engine, area) {
    var rows = state.records.filter(function (r) {
      return r.engine === engine && r.area === area && r.rank != null && r.rank <= 3;
    }).sort(function (a, b) {
      return a.rank - b.rank || a.keyword.localeCompare(b.keyword, "ko");
    });
    $("kwModalTitle").textContent =
      (ENGINE_LABEL[engine] || engine) + " " + (AREA_LABEL[area] || area) +
      " · TOP3 " + rows.length + "개";
    $("kwModalDesc").innerHTML =
      "이 엔진·영역에서 자사(삼삼엠투)가 검색 결과 1~3위 안에 노출된 키워드입니다. " +
      "상위 노출일수록 클릭 유입이 커서, 이 개수가 줄면 노출 경쟁에서 밀리고 있다는 신호입니다. " +
      "키워드를 누르면 순위 추이를 볼 수 있습니다.<br>" +
      '<span class="chip-legend"><span class="kw-chip legend-chip">브랜드 키워드</span>' +
      '<span class="kw-chip generic legend-chip">일반 키워드</span> — 색은 키워드 그룹 구분(순위와 무관)</span>';
    $("kwModalChips").innerHTML = rows.map(function (r) {
      return '<button type="button" class="kw-chip' + (isBrand(r.group) ? "" : " generic") +
        '" data-kw="' + esc(r.keyword) + '">' + esc(r.keyword) +
        " <b>" + r.rank + "위</b></button>";
    }).join("");
    $("kwModal").hidden = false;
    document.body.style.overflow = "hidden";
  }

  /* ---------- events ---------- */
  function bindEvents() {
    var searchTimer = null;
    $("fKeyword").addEventListener("input", function (e) {
      clearTimeout(searchTimer);
      var v = e.target.value;
      searchTimer = setTimeout(function () {
        state.filters.keyword = v;
        renderMatrix();
      }, 150);
    });

    $("fCategory").addEventListener("change", function (e) {
      state.filters.category = e.target.value;
      renderMatrix();
    });

    $("matrixHead").addEventListener("click", function (e) {
      var th = e.target.closest("th[data-sort]");
      if (!th) return;
      var key = th.dataset.sort;
      if (state.sort.key === key) state.sort.dir = -state.sort.dir;
      else state.sort = { key: key, dir: defaultDir(key) };
      renderMatrix();
    });

    $("viewSeg").addEventListener("click", function (e) {
      var btn = e.target.closest(".seg-btn");
      if (!btn) return;
      state.view = btn.dataset.view;
      document.querySelectorAll("#viewSeg .seg-btn").forEach(function (b) {
        b.classList.toggle("is-active", b.dataset.view === state.view);
      });
      renderMatrix();
    });

    /* 브리핑 패널: 행 클릭 → 추이 모달 */
    $("briefing").addEventListener("click", function (e) {
      var row = e.target.closest(".brief-row");
      if (!row) return;
      var r = state.records[Number(row.dataset.idx)];
      if (r) openModal(r);
    });

    /* 매트릭스 행/셀 클릭 → 해당 키워드 순위 추이 모달 */
    $("matrixBody").addEventListener("click", function (e) {
      if (e.target.closest("a")) return;
      var tr = e.target.closest("tr[data-kw]");
      if (!tr) return;
      openModalByKeyword(tr.dataset.kw);
    });

    /* 카드: 그룹 라벨 → 키워드 팝업 / TOP3 → TOP3 팝업 / 핵심 키워드 행 → 추이 모달 */
    $("summaryCards").addEventListener("click", function (e) {
      var link = e.target.closest(".kw-group-link");
      if (link) {
        var linkCard = e.target.closest(".card[data-engine]");
        if (link.classList.contains("top3-link") && linkCard) {
          openTop3Modal(linkCard.dataset.engine, linkCard.dataset.area);
        } else {
          openKwModal(link.dataset.group);
        }
        return;
      }
      var card = e.target.closest(".card[data-engine]");
      if (!card) return;
      var kwRow = e.target.closest(".card-kw-row");
      if (kwRow && kwRow.dataset.kw) {
        for (var i = 0; i < state.records.length; i++) {
          var r = state.records[i];
          if (r.engine === card.dataset.engine && r.area === card.dataset.area &&
              r.keyword === kwRow.dataset.kw) { openModal(r); return; }
        }
      }
    });

    /* 팝업 내 키워드 칩 → 해당 키워드 순위 추이 모달 */
    $("kwModal").addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) { closeKwModal(); return; }
      var chip = e.target.closest(".kw-chip");
      if (!chip || !chip.dataset.kw) return;
      closeKwModal();
      openModalByKeyword(chip.dataset.kw);
    });

    $("modal").addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) closeModal();
    });
    document.addEventListener("keydown", function (e) {
      if (e.key !== "Escape") return;
      if (!$("kwModal").hidden) closeKwModal();
      else if (!$("modal").hidden) closeModal();
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
      state.generatedAt = latest.generated_at || "";
      $("generatedAt").textContent = "마지막 수집: " + (latest.generated_at || "–");
      renderCards();
      renderBriefing();
      renderMatrix();
    }).catch(function (err) {
      $("summaryCards").innerHTML = '<div class="card card-blocked"><div class="card-blocked-msg">데이터를 불러오지 못했습니다: ' + esc(err.message) + "</div></div>";
    });
    bindEvents();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
