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
  var RANK_NONE_LABEL = {
    not_found: "미노출",
    no_section: "광고없음",
    blocked: "차단",
    parse_fail: "확인필요",
    error: "오류"
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

  var PAGE_SIZE = 100;

  var state = {
    records: [],
    trends: { days: [], series: {} },
    filters: { engine: "", area: "", group: "", status: "", keyword: "", delta: "" },
    sort: { key: null, dir: 1 },
    page: 1,
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

  /* 변동 분류 — blocked/error/parse_fail 은 '측정'이 아니므로 변동으로 치지 않는다
     (rank=null 을 이탈로 오독 금지). "down"=하락·이탈, "up"=상승·신규, null=해당 없음 */
  function changeKind(r) {
    if (BAD_STATUS[r.status] === 1) return null;
    var d = deltaOf(r);
    if (d === "lost" || (typeof d === "number" && d < 0)) return "down";
    if (d === "new" || (typeof d === "number" && d > 0)) return "up";
    return null;
  }

  /* 브리핑 정렬용 변동폭 — 이탈/신규는 계단 변동보다 항상 크게 취급 */
  function changeMagnitude(r) {
    var d = deltaOf(r);
    if (d === "new" || d === "lost") return 1000;
    return typeof d === "number" ? Math.abs(d) : 0;
  }

  /* ---------- summary cards ---------- */
  /* 카드에 순위를 직표시할 핵심 키워드(사용자 확정) — 데이터상 표기와 일치해야 매칭된다 */
  var PINNED_KEYWORDS = ["삼삼엠투", "33M2", "단기임대"];

  function countDelta(now, prev) {
    var d = now - prev;
    if (d > 0) return '<span class="delta-up">▲' + d + "</span>";
    if (d < 0) return '<span class="delta-down">▼' + (-d) + "</span>";
    return "";
  }

  /* 엔진별 데이터 시점 배지 — 최신 수집이 차단되면 대시보드는 '마지막 정상 런'을
     보여주므로(export_dashboard.py), 헤더의 마지막 수집 시각과 카드 데이터 시점이
     어긋날 수 있다(2026-08-07 사용자 혼동: 수정 전 데이터가 최신처럼 보임).
     6시간 이상 낡으면 시점을 명시해 오독을 막는다. */
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

      /* 첫 수집처럼 직전 런 데이터가 아예 없으면 증감 표시를 전부 숨긴다 */
      var hasPrev = rs.some(function (r) { return r.prev_rank != null; });

      /* 핵심 키워드 현재 순위 + 변동 */
      var pinned = PINNED_KEYWORDS.map(function (kw) {
        var r = null;
        for (var i = 0; i < rs.length; i++) {
          if (rs[i].keyword === kw) { r = rs[i]; break; }
        }
        if (!r) {
          /* 구글은 일반 키워드를 수집하지 않아 카드 간 구성이 어긋난다 →
             빈칸 대신 '측정 제외'로 이유를 보여줘 구성을 통일 */
          return '<div class="card-kw-row is-excluded" title="이 엔진에서는 측정하지 않는 키워드(무료 API 쿼터로 브랜드만 수집)">' +
            '<span class="card-kw">' + esc(kw) + "</span>" +
            '<span class="card-val"><span class="delta-flat">측정 제외</span></span></div>';
        }
        return '<div class="card-kw-row" data-kw="' + esc(kw) + '" title="순위 추이 보기">' +
          '<span class="card-kw">' + esc(kw) + "</span>" +
          '<span class="card-val">' + rankCell(r) + (hasPrev ? deltaCell(r) : "") + "</span></div>";
      }).join("");

      /* 노출 수치 + 전회 대비 증감 */
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

      /* 전회 대비 하락/이탈 한 줄 — 조치가 필요한 변화만 노출 */
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

      return '<div class="card card-clickable" data-engine="' + engine + '" data-area="' + area +
        '" title="클릭: 아래 테이블을 이 엔진·영역으로 필터">' + title +
        (pinned ? '<div class="card-kws">' + pinned + "</div>" : "") +
        '<div class="card-rows">' +
        '<div class="card-row"><span class="kw-group-link" data-group="brand" title="키워드 목록 보기">브랜드 노출</span>' + exposure(brand) + "</div>" +
        '<div class="card-row"><span class="kw-group-link" data-group="generic" title="키워드 목록 보기">일반 노출</span>' + exposure(generic) + "</div>" +
        '<div class="card-row"><span class="kw-group-link top3-link" title="TOP3 설명·키워드 보기">TOP3</span><span class="card-val"><b>' + top3 + "개</b>" +
        (hasPrev ? countDelta(top3, top3prev) : "") + "</span></div>" +
        '<div class="card-row"><span class="kw-group-link bad-link" title="수집이상 레코드를 테이블에서 보기">수집이상</span>' +
        (bad > 0 ? '<b class="bad-count">' + bad + "건</b>" : "<b>0건</b>") +
        "</div></div>" + dropLine + "</div>";
    }).join("");
    $("summaryCards").innerHTML = html;
  }

  /* ---------- change briefing ---------- */
  /* 직전 수집 대비 변동 브리핑 — "뭐가 빠졌고 뭐가 올랐나"를 테이블 스캔 없이 바로 보여준다.
     검색량(sv) 내림차순 → 동률이면 변동폭 큰 순으로 상위 8개만. */
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
      var sv = r.sv == null ? "–" : Math.round(r.sv / 30).toLocaleString() + "/일";
      return '<button type="button" class="brief-row" data-idx="' + r._idx + '" title="순위 추이 보기">' +
        '<span class="brief-kw">' + esc(r.keyword) + "</span>" +
        '<span class="brief-meta">' + (ENGINE_LABEL[r.engine] || esc(r.engine)) + "/" + (AREA_LABEL[r.area] || esc(r.area)) + "</span>" +
        '<span class="brief-move">' + briefMove(r) + "</span>" +
        '<span class="brief-sv" title="네이버 검색량(일평균)">검색량 ' + sv + "</span></button>";
    }).join("") + "</div>";
    if (rows.length > BRIEF_LIMIT) {
      html += '<button type="button" class="brief-more" data-delta="' + kind + '">테이블에서 전체 보기 →</button>';
    }
    return html + "</div>";
  }

  function renderBriefing() {
    var box = $("briefing");
    /* 직전 런 데이터가 아예 없으면(첫 수집) 브리핑 자체를 숨긴다 */
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
      if (f.status === "bad") {
        if (BAD_STATUS[r.status] !== 1) return false;
      } else if (f.status && r.status !== f.status) return false;
      if (kw && r.keyword.toLowerCase().indexOf(kw) === -1) return false;
      if (f.delta === "top3") {
        if (r.rank == null || r.rank > 3) return false;
      } else if (f.delta && changeKind(r) !== f.delta) return false;
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
      if (s.key === "sv") {
        va = a.sv == null ? null : a.sv; vb = b.sv == null ? null : b.sv;
        if (va === null && vb === null) return 0;
        if (va === null) return 1; /* 검색량 미확인은 항상 하단 */
        if (vb === null) return -1;
        return dir * (vb - va); /* 첫 클릭 = 검색량 큰 순 */
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
    /* '1위 / 20' = 스캔한 20개 결과 중 1위 — 결과수 별도 컬럼의 의미 혼동을 흡수
       (2026-08-07 사용자 피드백). 권외도 '/ 20'을 붙여 '20개 안에 없음'을 드러낸다. */
    var suffix = r.total > 0 ? ' <span class="rank-total">/ ' + r.total + "</span>" : "";
    if (r.rank != null) return '<span class="rank-val">' + r.rank + "위</span>" + suffix;
    var label = RANK_NONE_LABEL[r.status] || "–";
    return '<span class="rank-none s-' + esc(r.status) + '">' + label + "</span>" +
      (r.status === "not_found" ? suffix : "");
  }

  /* 네이버 키워드도구 최근 30일 검색수(PC+MO) ÷ 30 = 일평균 — daily 런에서 갱신.
     띄어쓰기 변형('삼삼엠투 후기')은 API 정규화 특성상 무공백형과 같은 값이다. */
  function svCell(r) {
    if (r.sv == null) return '<span class="delta-flat">–</span>';
    var daily = Math.round(r.sv / 30);
    return '<span class="sv-val" title="네이버 최근 30일 총 ' + r.sv.toLocaleString() + '회">' +
      daily.toLocaleString() + "</span>";
  }

  function renderTable() {
    var rows = sortRecords(filteredRecords());
    var total = rows.length;
    var pageCount = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (state.page > pageCount) state.page = pageCount;
    if (state.page < 1) state.page = 1;
    var start = (state.page - 1) * PAGE_SIZE;
    var pageRows = rows.slice(start, start + PAGE_SIZE);

    var body = $("tableBody");
    var html = "";
    for (var i = 0; i < pageRows.length; i++) {
      var r = pageRows[i];
      var groupBrand = isBrand(r.group);
      html += '<tr data-idx="' + r._idx + '">' +
        '<td class="kw">' + esc(r.keyword) + "</td>" +
        "<td><span class=\"badge " + (groupBrand ? "badge-group-brand\">브랜드" : "badge-group-generic\">일반") + "</span></td>" +
        '<td class="num">' + svCell(r) + "</td>" +
        "<td>" + (ENGINE_LABEL[r.engine] || esc(r.engine)) + "</td>" +
        "<td>" + (AREA_LABEL[r.area] || esc(r.area)) + "</td>" +
        '<td class="num">' + rankCell(r) + "</td>" +
        "<td>" + deltaCell(r) + "</td>" +
        "<td>" + esc(r.section || "–") + "</td>" +
        '<td><span class="badge badge-s-' + esc(r.status) + '">' + (STATUS_LABEL[r.status] || esc(r.status)) + "</span></td>" +
        "<td>" + sparklineSVG(r.engine, r.area, r.keyword) + "</td>" +
        '<td class="go">' + goBtn(r.engine, r.keyword) + "</td>" +
        '<td class="time">' + esc(r.collected_at || "") + "</td>" +
        "</tr>";
    }
    body.innerHTML = html;
    $("emptyMsg").hidden = total > 0;

    var label = total === 0 ? "0건"
      : (start + 1).toLocaleString() + "–" + (start + pageRows.length).toLocaleString() +
        " / " + total.toLocaleString() + "건";
    if (total !== state.records.length) label += " (전체 " + state.records.length.toLocaleString() + "건)";
    $("rowCount").textContent = label;

    renderPager(pageCount);
  }

  /* ---------- pagination ---------- */
  /* « 이전 · 1 … 현재±2 … 끝 · 다음 » — 필터·정렬 변경 시 1페이지로 리셋 */
  function renderPager(pageCount) {
    var pager = $("pager");
    if (pageCount <= 1) { pager.hidden = true; pager.innerHTML = ""; return; }
    var cur = state.page;
    var pages = [];
    for (var p = 1; p <= pageCount; p++) {
      if (p === 1 || p === pageCount || Math.abs(p - cur) <= 2) pages.push(p);
    }
    var html = '<button type="button" class="page-btn page-prev" data-page="' + (cur - 1) + '"' +
      (cur === 1 ? " disabled" : "") + ">« 이전</button>";
    var last = 0;
    pages.forEach(function (p) {
      if (p - last > 1) html += '<span class="page-gap">…</span>';
      html += '<button type="button" class="page-btn' + (p === cur ? " is-current" : "") +
        '" data-page="' + p + '"' + (p === cur ? ' aria-current="page"' : "") + ">" + p + "</button>";
      last = p;
    });
    html += '<button type="button" class="page-btn page-next" data-page="' + (cur + 1) + '"' +
      (cur === pageCount ? " disabled" : "") + ">다음 »</button>";
    pager.innerHTML = html;
    pager.hidden = false;
  }

  /* 필터·정렬이 바뀌면 항상 1페이지부터 */
  function resetAndRender() {
    state.page = 1;
    renderTable();
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
      var series = state.trends.series[key];
      /* 값이 전부 null 인 시리즈(구글 광고 blocked 이력 등)는 범례 노이즈 → 제외 */
      if (!series.some(function (v) { return v != null; })) return;
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

  /* TOP3 = 해당 엔진·영역에서 자사가 검색 결과 1~3위 안에 노출된 키워드 수.
     팝업으로 정의와 실제 키워드 목록(현재 순위 포함)을 보여준다. */
  function openTop3Modal(engine, area) {
    var rows = state.records.filter(function (r) {
      return r.engine === engine && r.area === area && r.rank != null && r.rank <= 3;
    }).sort(function (a, b) {
      return a.rank - b.rank || a.keyword.localeCompare(b.keyword, "ko");
    });
    $("kwModalTitle").textContent =
      (ENGINE_LABEL[engine] || engine) + " " + (AREA_LABEL[area] || area) +
      " · TOP3 " + rows.length + "개";
    /* 칩 색 = 키워드 그룹(순위와 무관) — 범례 없이는 오독됨(2026-08-07 사용자 질문) */
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
    [["fEngine", "engine"], ["fArea", "area"], ["fGroup", "group"], ["fStatus", "status"]]
      .forEach(function (pair) {
        $(pair[0]).addEventListener("change", function (e) {
          state.filters[pair[1]] = e.target.value;
          resetAndRender();
        });
      });

    var searchTimer = null;
    $("fKeyword").addEventListener("input", function (e) {
      clearTimeout(searchTimer);
      var v = e.target.value;
      searchTimer = setTimeout(function () {
        state.filters.keyword = v;
        resetAndRender();
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
        resetAndRender();
      });
    });

    /* 변동 퀵필터 칩 — 다른 필터와 AND 조합 */
    function setDeltaFilter(v) {
      state.filters.delta = v;
      document.querySelectorAll("#deltaChips .chip").forEach(function (c) {
        c.classList.toggle("is-active", c.dataset.delta === v);
      });
      resetAndRender();
    }
    $("deltaChips").addEventListener("click", function (e) {
      var chip = e.target.closest(".chip");
      if (chip) setDeltaFilter(chip.dataset.delta);
    });

    /* 브리핑 패널: 행 클릭 → 추이 모달 / '전체 보기' → 해당 변동 퀵필터 + 테이블로 */
    $("briefing").addEventListener("click", function (e) {
      var more = e.target.closest(".brief-more");
      if (more) {
        setDeltaFilter(more.dataset.delta);
        $("filterBar").scrollIntoView({ behavior: "smooth", block: "start" });
        return;
      }
      var row = e.target.closest(".brief-row");
      if (!row) return;
      var r = state.records[Number(row.dataset.idx)];
      if (r) openModal(r);
    });

    /* 페이저 — 페이지 이동 시 테이블 상단으로 스크롤 */
    $("pager").addEventListener("click", function (e) {
      var btn = e.target.closest(".page-btn");
      if (!btn || btn.disabled) return;
      var p = Number(btn.dataset.page);
      if (!p || p === state.page) return;
      state.page = p;
      renderTable();
      $("rankTable").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    $("tableBody").addEventListener("click", function (e) {
      var tr = e.target.closest("tr[data-idx]");
      if (e.target.closest("a")) return; /* 보러가기 링크 클릭은 모달을 띄우지 않는다 */
      if (!tr) return;
      var r = state.records[Number(tr.dataset.idx)];
      if (r) openModal(r);
    });

    /* 카드 클릭: 그룹 라벨 → 키워드 팝업 / 핵심 키워드 행 → 추이 모달 /
       그 외 → 아래 테이블을 해당 엔진·영역으로 필터 */
    $("summaryCards").addEventListener("click", function (e) {
      var link = e.target.closest(".kw-group-link");
      if (link) {
        var linkCard = e.target.closest(".card[data-engine]");
        if (link.classList.contains("top3-link") && linkCard) {
          openTop3Modal(linkCard.dataset.engine, linkCard.dataset.area);
        } else if (link.classList.contains("bad-link") && linkCard) {
          /* 수집이상 → 테이블을 해당 엔진·영역의 이상 레코드(차단·구조변경·오류)로 필터 */
          $("fEngine").value = state.filters.engine = linkCard.dataset.engine;
          $("fArea").value = state.filters.area = linkCard.dataset.area;
          $("fStatus").value = state.filters.status = "bad";
          resetAndRender();
          $("filterBar").scrollIntoView({ behavior: "smooth", block: "start" });
        } else {
          openKwModal(link.dataset.group);
        }
        return;
      }
      var card = e.target.closest(".card[data-engine]");
      if (!card) return;
      var kwRow = e.target.closest(".card-kw-row");
      if (kwRow) {
        for (var i = 0; i < state.records.length; i++) {
          var r = state.records[i];
          if (r.engine === card.dataset.engine && r.area === card.dataset.area &&
              r.keyword === kwRow.dataset.kw) { openModal(r); return; }
        }
        return;
      }
      if (card.classList.contains("card-blocked")) return;
      $("fEngine").value = state.filters.engine = card.dataset.engine;
      $("fArea").value = state.filters.area = card.dataset.area;
      resetAndRender();
      $("filterBar").scrollIntoView({ behavior: "smooth", block: "start" });
    });

    /* 팝업 내 키워드 칩 → 해당 키워드 순위 추이 모달 */
    $("kwModal").addEventListener("click", function (e) {
      if (e.target.hasAttribute("data-close")) { closeKwModal(); return; }
      var chip = e.target.closest(".kw-chip");
      if (!chip || !chip.dataset.kw) return; /* 범례 칩(data-kw 없음)은 무시 */
      var rec = null;
      for (var i = 0; i < state.records.length; i++) {
        if (state.records[i].keyword === chip.dataset.kw) { rec = state.records[i]; break; }
      }
      closeKwModal();
      if (rec) openModal(rec);
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
      renderTable();
    }).catch(function (err) {
      $("summaryCards").innerHTML = '<div class="card card-blocked"><div class="card-blocked-msg">데이터를 불러오지 못했습니다: ' + esc(err.message) + "</div></div>";
    });
    bindEvents();
  }

  document.addEventListener("DOMContentLoaded", init);
})();
