const API_KEY = ""; // Google Sheets API Key
const SPREADSHEET_ID = ""; // Google Sheets ID

let dashboardData = null;
let selectedProductIndex = 0;
let mainChart = null;
let mallPriceChart = null;
let selectedCategory = '전체';
let chartDays = 7;
let currentPage = 1;
const itemsPerPageNormal = 10;
let currentFilteredHistory = [];
let currentMallCategory = null;
let currentMall = null;
let selectedMallProductIndex = 0;

function switchView(viewName) {
    const views = ['priceView', 'rankingView', 'mallReportView'];
    views.forEach(v => {
        const el = document.getElementById(v);
        if (el) el.classList.remove('active');
    });
    
    const targetView = document.getElementById(viewName);
    if (targetView) targetView.classList.add('active');

    // 상단 가로 탭 버튼 활성화 상태 업데이트
    document.querySelectorAll('.view-tab').forEach(tab => tab.classList.remove('active'));
    if (viewName === 'priceView') document.getElementById('tabPrice')?.classList.add('active');
    if (viewName === 'rankingView') document.getElementById('tabRanking')?.classList.add('active');

    // 사이드바 글로벌 네비게이션 활성화
    document.querySelectorAll('.nav-item').forEach(item => item.classList.remove('active'));
    if (viewName === 'mallReportView') {
        document.getElementById('nav-mall')?.classList.add('active');
    } else {
        document.getElementById('nav-price')?.classList.add('active');
    }

    // 사이드바 콘텐츠 전환
    const productSidebar = document.getElementById('product-sidebar-content');
    const mallSidebar = document.getElementById('mall-sidebar-content');
    const header = document.querySelector('header');

    if (viewName === 'mallReportView') {
        if (productSidebar) productSidebar.style.display = 'none';
        if (mallSidebar) mallSidebar.style.display = 'block';
        if (header) header.style.display = 'none'; 
        renderMallCategoryTabs();
    } else {
        if (productSidebar) productSidebar.style.display = 'block';
        if (mallSidebar) mallSidebar.style.display = 'none';
        if (header) header.style.display = 'flex';
        if (viewName === 'priceView') refreshChartView();
    }

    if (window.innerWidth <= 768) toggleSidebar(false);
}

function toggleSidebar(open) {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebarOverlay');
    if (sidebar) sidebar.classList.toggle('open', open);
    if (overlay) overlay.classList.toggle('show', open);
}

function downloadExcel() {
    const checkedCbs = document.querySelectorAll('.product-checkbox:checked');
    if (checkedCbs.length === 0) {
        alert("다운로드할 상품을 최소 하나 이상 선택해주세요.");
        return;
    }

    const wb = XLSX.utils.book_new();
    const combinedData = [
        ["모델명", "날짜", "시간", "가격", "판매처"]
    ];
    
    checkedCbs.forEach(cb => {
        const idx = parseInt(cb.dataset.index);
        const p = dashboardData.products[idx];
        if (!p || !p.history) return;

        p.history.slice().reverse().forEach(h => {
            const dt = new Date(h.t);
            const dateStr = dt.getFullYear() + '-' + String(dt.getMonth() + 1).padStart(2, '0') + '-' + String(dt.getDate()).padStart(2, '0');
            const timeStr = String(dt.getHours()).padStart(2, '0') + ':' + String(dt.getMinutes()).padStart(2, '0');
            
            combinedData.push([
                p.name,
                dateStr,
                timeStr,
                Number(h.p), // 가격을 숫자로 강제하여 엑셀에서 연산 가능하게 함
                formatSellerName(h.s || p.seller) || "네이버"
            ]);
        });
    });

    const ws = XLSX.utils.aoa_to_sheet(combinedData);
    XLSX.utils.book_append_sheet(wb, ws, "가격_추적_데이터");

    // [추가] 키워드 랭킹 데이터 시트 추가
    if (dashboardData.rankings && Object.keys(dashboardData.rankings).length > 0) {
        const rankingData = [
            ["키워드", "순위", "쇼핑몰명", "상품명", "가격", "링크"]
        ];

        Object.keys(dashboardData.rankings).sort().forEach(kw => {
            const items = dashboardData.rankings[kw];
            items.forEach((item, idx) => {
                rankingData.push([
                    kw,
                    idx + 1,
                    item.seller_name,
                    item.title,
                    item.price,
                    item.product_url || item.url
                ]);
            });
        });

        const wsRank = XLSX.utils.aoa_to_sheet(rankingData);
        XLSX.utils.book_append_sheet(wb, wsRank, "키워드_랭킹_데이터");
    }

    XLSX.writeFile(wb, `price_tracker_combined_${new Date().toISOString().split('T')[0]}.xlsx`);
}

async function init() {
    console.log("Initializing dashboard...");
    
    try {
        const url = 'dashboard_data.json?v=' + encodeURIComponent(new Date().getTime());
        const response = await fetch(url);
        if (!response.ok) throw new Error(`Fetch failed: ${response.status}`);

        dashboardData = await response.json();
        console.log("Data loaded:", dashboardData);

        if (dashboardData.gsheet_id) {
            const linkEl = document.getElementById('gsheetLink');
            const configLinkEl = document.getElementById('configSheetLink');
            if (linkEl) {
                linkEl.href = `https://docs.google.com/spreadsheets/d/${dashboardData.gsheet_id}`;
                linkEl.style.display = 'block';
            }
            if (configLinkEl) {
                // 특정 탭으로 바로 이동하는 gid는 시트마다 다르므로 기본 주소로 연결하되 문구로 유도
                configLinkEl.href = `https://docs.google.com/spreadsheets/d/${dashboardData.gsheet_id}/edit#gid=0`;
                configLinkEl.style.display = 'block';
            }
        }

        if (!dashboardData.products || dashboardData.products.length === 0) {
            console.warn("No products found in dashboard_data.json, but continuing for Mall Tracker.");
            renderCategoryTabs();
            renderProductList();
            if (dashboardData.mall_reports) {
                renderMallCategoryTabs();
            }
            return; 
        }

        renderCategoryTabs();
        renderProductList();

        const firstVisible = dashboardData.products.findIndex(p => selectedCategory === '전체' || p.category === selectedCategory);
        selectProduct(firstVisible >= 0 ? firstVisible : 0);
    } catch (e) {
        console.error("Init error:", e);
        const list = document.getElementById('productList');
        if (list) list.innerHTML = `<div style="padding: 20px; color: #ef4444; font-size: 13px;">⚠️ ${e.message}</div>`;
    }
}

function formatPrice(num) {
    if (!num) return '-';
    return new Intl.NumberFormat('ko-KR').format(num) + '원';
}

function formatKoreanDate(dateStr) {
    if (!dateStr) return '날짜 정보 없음';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return dateStr || '날짜 정보 없음';
    const y = d.getFullYear();
    const m = d.getMonth() + 1;
    const day = d.getDate();
    const hh = d.getHours();
    const mm = String(d.getMinutes()).padStart(2, '0');
    return `${y}년 ${m}월 ${day}일 ${hh}시 ${mm}분`;
}

function timeAgo(dateStr) {
    if (!dateStr) return '';
    const d = new Date(dateStr);
    if (isNaN(d.getTime())) return '';
    const now = new Date();
    const diff = Math.floor((now - d) / 1000);
    if (diff < 60) return '방금 전';
    if (diff < 3600) return Math.floor(diff / 60) + '분 전';
    if (diff < 86400) return Math.floor(diff / 3600) + '시간 전';
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + '일 전';
    return Math.floor(diff / (86400 * 7)) + '주 전';
}

function toggleSelectAll(el) {
    document.querySelectorAll('.product-checkbox').forEach(cb => cb.checked = el.checked);
    updateCheckedCount();
}

function updateCheckedCount() {
    const count = document.querySelectorAll('.product-checkbox:checked').length;
    const el = document.getElementById('checkedCount');
    if (el) el.textContent = count + '개';
}

function renderCategoryTabs() {
    const container = document.getElementById('categoryTabs');
    if (!container) return;
    const categories = ['전체', ...new Set(dashboardData.products.map(p => p.category).filter(c => c))];
    container.innerHTML = categories.map(cat => `
        <div class="category-pill ${cat === selectedCategory ? 'active' : ''}" 
             onclick="filterCategory('${cat}')">${cat}</div>
    `).join('');
}

function filterCategory(cat) {
    selectedCategory = cat;
    renderCategoryTabs();
    renderProductList();
    const firstIdx = dashboardData.products.findIndex(p => selectedCategory === '전체' || p.category === selectedCategory);
    if (firstIdx >= 0) selectProduct(firstIdx);
}

function renderProductList() {
    const list = document.getElementById('productList');
    if (!list) return;
    let filtered = dashboardData.products.map((p, i) => ({ ...p, originalIndex: i }))
        .filter(p => selectedCategory === '전체' || p.category === selectedCategory);

    const sortOption = document.getElementById('sortOption')?.value || 'default';
    if (sortOption === 'price_asc') filtered.sort((a, b) => (a.current_price || 9e9) - (b.current_price || 9e9));
    else if (sortOption === 'price_desc') filtered.sort((a, b) => (b.current_price || 0) - (a.current_price || 0));
    else if (sortOption === 'name_asc') filtered.sort((a, b) => a.name.localeCompare(b.name));

    list.innerHTML = filtered.map(p => {
        const change = p.change_pct ? p.change_pct.toFixed(1) + '%' : '0.0%';
        const badgeClass = p.status === 'PRICE_DOWN' ? 'badge-down' : (p.status === 'PRICE_UP' ? 'badge-up' : 'badge-none');
        const trendIcon = p.status === 'PRICE_DOWN' ? '⬇️' : (p.status === 'PRICE_UP' ? '⬆️' : '➖');
        return `
            <li class="product-item ${p.originalIndex === selectedProductIndex ? 'active' : ''}" 
                onclick="selectProduct(${p.originalIndex})">
                <input type="checkbox" class="product-checkbox" data-index="${p.originalIndex}" onclick="event.stopPropagation(); updateCheckedCount();">
                <img src="${p.image_url || ''}" class="product-thumb" loading="lazy" onerror="this.src='https://search.shopping.naver.com/static/img/catalog/no_image.png'">
                <div class="product-info">
                    <div style="display: flex; align-items: center; gap: 6px; flex-wrap: wrap;">
                        <span class="product-name">${p.name}</span>
                        ${p.is_unauthorized ? '<span class="badge-unauthorized" title="공식 이미지 무단 도용 의심">⚠️ 도용예정</span>' : (p.product_code && p.product_code !== 'IMAGE_MISUSE_DETECTED' ? '<span class="badge-protection" title="공식 이미지 매칭">Match</span>' : '')}
                    </div>
                    <div class="product-meta">
                        <span class="product-price">${formatPrice(p.current_price)}</span>
                        <span class="change-badge ${badgeClass}">${trendIcon} ${change}</span>
                    </div>
                </div>
            </li>
        `;
    }).join('');
    updateCheckedCount();
}

function selectProduct(index) {
    selectedProductIndex = index;
    const product = dashboardData.products[index];
    if (!product) return;

    document.querySelectorAll('.product-item').forEach(el => {
        const cb = el.querySelector('.product-checkbox');
        if (cb) el.classList.toggle('active', parseInt(cb.dataset.index) === index);
    });

    const titleEl = document.getElementById('selectedTitle');
    if (titleEl) titleEl.innerText = product.name;

    const imgEl = document.getElementById('selectedImage');
    if (imgEl) {
        imgEl.src = product.image_url || 'https://search.shopping.naver.com/static/img/catalog/no_image.png';
        imgEl.onerror = () => { imgEl.src = 'https://search.shopping.naver.com/static/img/catalog/no_image.png'; };
    }

    const rankHtml = product.search_rank ? ` <span style="margin:0 8px; color:var(--glass-border);">|</span> <span style="color:var(--toss-blue); font-weight:700;">네이버랭킹 ${product.search_rank}위</span>` : '';
    const keywordHtml = ` <span style="margin:0 8px; color:var(--glass-border);">|</span> <span style="font-size:12px; color:var(--text-muted);">키워드: "${product.rank_query || product.name}"</span>`;
    
    let sellerHtml = `<span class="seller-tag">${formatSellerName(product.seller)}</span>`;
    if (product.mall_link) {
        sellerHtml = `<span class="seller-tag clickable" title="쇼핑몰 리포트로 이동" onclick="goToMallReport('${product.mall_link.category}', '${product.mall_link.mall}')">${formatSellerName(product.seller)} ↗</span>`;
    }
    
    let statusHtml = '';
    if (product.is_unauthorized) {
        statusHtml = ` <span style="margin:0 8px; color:var(--glass-border);">|</span> <span style="background:rgba(239, 68, 68, 0.1); color:#ef4444; padding:2px 10px; border-radius:6px; font-weight:800; font-size:12px; border:1px solid rgba(239,68,68,0.2);">🚨 이미지 무단 도용 의심</span>`;
    } else if (product.product_code && product.product_code !== 'IMAGE_MISUSE_DETECTED') {
        statusHtml = ` <span style="margin:0 8px; color:var(--glass-border);">|</span> <span style="background:rgba(16, 185, 129, 0.1); color:#10b981; padding:2px 8px; border-radius:6px; font-weight:700; font-size:11px;">🛡️ 공식 이미지 확인됨</span>`;
    }
    
    const sellerInfoEl = document.getElementById('sellerInfo');
    if (sellerInfoEl) sellerInfoEl.innerHTML = `${sellerHtml} 수집 중${rankHtml}${keywordHtml}${statusHtml}`;
    
    const setVal = (id, val) => { const el = document.getElementById(id); if (el) el.innerText = val; };
    setVal('currentPriceStat', formatPrice(product.current_price));
    setVal('allTimeLow', formatPrice(product.all_time_low));
    setVal('allTimeHigh', formatPrice(product.all_time_high));
    setVal('avg7d', formatPrice(product.avg_7d));
    setVal('avg30d', formatPrice(product.avg_30d));

    const setDiff = (id, cur, avg) => {
        const el = document.getElementById(id);
        if (!el) return;
        if (!avg) { el.innerText = '-'; return; }
        const diff = cur - avg;
        const pct = ((diff / avg) * 100).toFixed(1);
        if (diff < 0) { el.innerText = `평균대비 ${Math.abs(pct)}% 낮음 ✨`; el.style.color = 'var(--price-down)'; }
        else if (diff > 0) { el.innerText = `평균대비 ${pct}% 높음`; el.style.color = 'var(--price-up)'; }
        else { el.innerText = '평균가와 동일'; el.style.color = 'var(--text-muted)'; }
    };
    setDiff('avg7dDiff', product.current_price, product.avg_7d);
    setDiff('avg30dDiff', product.current_price, product.avg_30d);

    const badgeContainer = document.getElementById('priceBadgeContainer');
    if (badgeContainer) {
        if (product.current_price <= product.all_time_low && product.all_time_low > 0) {
            badgeContainer.innerHTML = `<div class="chart-badge" style="background:rgba(49,130,247,0.1); color:#3182f7;">✨ 역대 최저가 달성 중!</div>`;
        } else if (product.all_time_low > 0) {
            const pct = (((product.current_price - product.all_time_low) / product.all_time_low) * 100).toFixed(1);
            badgeContainer.innerHTML = `<div class="chart-badge">역대 최저가 대비 +${pct}%</div>`;
        } else {
            badgeContainer.innerHTML = '';
        }
    }

    renderRankingView(product.rank_query || product.name);
    
    const catalogBtn = document.getElementById('catalogBtn');
    if (catalogBtn) {
        if (product.product_url) {
            catalogBtn.href = product.product_url;
            catalogBtn.style.display = 'inline-flex';
            catalogBtn.innerHTML = '카탈로그 바로가기 ↗';
        } else {
            catalogBtn.style.display = 'none';
        }
    }

    currentPage = 1;
    refreshChartView();
}

function refreshChartView() {
    const product = dashboardData?.products?.[selectedProductIndex];
    if (!product) return;
    let hist = product.history || [];
    if (chartDays > 0) {
        const cutoff = new Date(); cutoff.setDate(cutoff.getDate() - chartDays);
        hist = hist.filter(h => new Date(h.t) >= cutoff);
    }
    renderChart(hist);
    currentFilteredHistory = hist.slice().reverse();
    renderTable();
}

function renderChart(history) {
    const ctx = document.getElementById('priceChart')?.getContext('2d');
    if (!ctx) return;
    if (mainChart) mainChart.destroy();
    mainChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: history.map(h => { const d = new Date(h.t); return `${d.getMonth()+1}/${d.getDate()} ${d.getHours()}시`; }),
            datasets: [{ 
                data: history.map(h => h.p), 
                sellers: history.map(h => h.s),
                borderColor: '#3182f7', 
                borderWidth: 3, 
                pointRadius: 4, 
                pointHoverRadius: 6,
                fill: true, 
                backgroundColor: 'rgba(49,130,247,0.1)', 
                tension: 0.4 
            }]
        },
        options: { 
            responsive: true, 
            maintainAspectRatio: false, 
            plugins: { 
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function(context) {
                            let label = context.dataset.label || '';
                            if (label) label += ': ';
                            if (context.parsed.y !== null) {
                                label += new Intl.NumberFormat('ko-KR').format(context.parsed.y) + '원';
                            }
                            const seller = context.dataset.sellers[context.dataIndex];
                            if (seller) {
                                label += ` (${formatSellerName(seller)})`;
                            }
                            return label;
                        }
                    }
                }
            }, 
            scales: { 
                x: { display: false }, 
                y: { ticks: { callback: v => v.toLocaleString() } } 
            } 
        }
    });
}

function renderTable() {
    const tbody = document.querySelector('#historyTable tbody');
    if (!tbody) return;
    const product = dashboardData?.products?.[selectedProductIndex];
    if (!product) return;
    
    const start = (currentPage - 1) * itemsPerPageNormal;
    const pageData = currentFilteredHistory.slice(start, start + itemsPerPageNormal);
    tbody.innerHTML = pageData.map(h => `
        <tr>
            <td>
                <div class="time-ago">${timeAgo(h.t)}</div>
                <div class="time-label">${formatKoreanDate(h.t)}</div>
            </td>
            <td style="font-weight:700">${formatPrice(h.p)}</td>
            <td>${formatPrice(h.p)}</td>
            <td style="color:var(--toss-blue); font-weight:600;">
                <a href="${h.u || product.product_url || '#'}" target="_blank" style="color:inherit; text-decoration:none;">
                    ${formatSellerName(h.s || product.seller)} ↗
                </a>
            </td>
            <td>정상</td>
        </tr>`).join('');
    renderPagination();
}

let selectedRankingCategory = null;

function renderRankingView(kw) {
    const container = document.getElementById('ranking-list-container');
    const tabContainer = document.getElementById('ranking-keyword-tabs');
    if (!container) return;

    if (!dashboardData?.rankings) {
        container.innerHTML = '<div style="text-align: center; color: var(--text-muted); padding: 40px;">랭킹 데이터가 없습니다.</div>';
        return;
    }

    // 1. 키워드별 카테고리 맵핑 생성 (상품 데이터를 기반으로)
    const keywordToCat = {};
    if (dashboardData.products) {
        dashboardData.products.forEach(p => {
            if (p.rank_query) keywordToCat[p.rank_query] = p.category;
            // '갤럭시' 키워드가 포함된 키워드와 미포함 키워드 모두 같은 카테고리로 맵핑 (단어 포함 여부로 보정)
            const baseKw = p.rank_query.replace('갤럭시 ', '');
            keywordToCat[baseKw] = p.category;
            keywordToCat['갤럭시 ' + baseKw] = p.category;
        });
    }

    const allKeywords = Object.keys(dashboardData.rankings);
    const catToKeywords = {};
    allKeywords.forEach(k => {
        let cat = keywordToCat[k] || '기타';
        if (!catToKeywords[cat]) catToKeywords[cat] = [];
        catToKeywords[cat].push(k);
    });

    const categories = Object.keys(catToKeywords).sort();
    if (!selectedRankingCategory && categories.length > 0) {
        selectedRankingCategory = categories[0];
    }

    // 2. 카테고리 상단 탭 렌더링
    let html = `<div class="ranking-category-tabs" style="display: flex; gap: 10px; margin-bottom: 12px; border-bottom: 1px solid var(--glass-border); padding-bottom: 12px;">`;
    categories.forEach(cat => {
        const isActive = selectedRankingCategory === cat;
        html += `<div class="rank-cat-tab ${isActive ? 'active' : ''}" 
                      style="cursor: pointer; padding: 6px 16px; border-radius: 20px; font-size: 13px; font-weight: 700; background: ${isActive ? 'var(--toss-blue)' : '#f2f4f6'}; color: ${isActive ? 'white' : 'var(--text-muted)'};"
                      onclick="selectedRankingCategory='${cat}'; renderRankingView('${catToKeywords[cat][0]}')">
                    ${cat}
                 </div>`;
    });
    html += `</div>`;

    // 3. 세부 키워드 탭 (갤럭시 포함/미포함 등) 렌더링
    const currentKeywords = (catToKeywords[selectedRankingCategory] || []).sort((a, b) => a.localeCompare(b));
    if (currentKeywords.length > 1) {
        html += `<div class="ranking-sub-tabs" style="display: flex; gap: 8px; margin-bottom: 24px; flex-wrap: wrap;">`;
        currentKeywords.forEach(k => {
            const isSubActive = k === kw;
            html += `<div class="keyword-pill ${isSubActive ? 'active' : ''}" 
                          style="cursor: pointer; padding: 4px 12px; border-radius: 8px; font-size: 12px; background: ${isSubActive ? 'rgba(49,130,247,0.1)' : 'white'}; border: 1px solid ${isSubActive ? 'var(--toss-blue)' : 'var(--glass-border)'}; color: ${isSubActive ? 'var(--toss-blue)' : 'var(--text-muted)'}; font-weight: 600;"
                          onclick="renderRankingView('${k}')">
                        ${k}
                     </div>`;
        });
        html += `</div>`;
    }

    if (tabContainer) tabContainer.innerHTML = html;

    // 만약 현재 kw가 해당 카테고리에 없으면 첫번째 키워드로 변경
    if (kw && !currentKeywords.includes(kw)) {
        kw = currentKeywords[0];
    }
    if (!kw && currentKeywords.length > 0) kw = currentKeywords[0];

    // 4. 랭킹 리스트 렌더링
    if (!kw || !dashboardData.rankings[kw]) {
        container.innerHTML = `
            <div style="text-align: center; color: var(--text-muted); padding: 60px 20px;">
                검색 데이터가 없습니다.
            </div>
        `;
        return;
    }

    const rankings = dashboardData.rankings[kw].slice(0, 10);
    container.innerHTML = rankings.map((item, idx) => `
        <div class="ranking-card" style="display: flex; align-items: center; gap: 16px; padding: 16px; border-bottom: 1px solid var(--glass-border); cursor: pointer; transition: background 0.2s;" onclick="window.open('${item.product_url || item.url}', '_blank')">
            <div class="rank-num ${idx === 0 ? 'top1' : (idx < 3 ? 'top3' : '')}" style="width: 24px; text-align: center; font-weight: 800; color: ${idx === 0 ? '#ffb800' : (idx < 3 ? 'var(--toss-blue)' : 'var(--text-muted)')}; font-size: 18px;">${idx + 1}</div>
            <img src="${item.image_url || ''}" style="width: 60px; height: 60px; object-fit: contain; border-radius: 8px; border: 1px solid var(--glass-border);" onerror="this.src='https://search.shopping.naver.com/static/img/catalog/no_image.png'">
            <div class="ranking-card-body" style="flex: 1;">
                <div class="mall" style="font-size: 11px; color: var(--toss-blue); font-weight: 700;">${item.seller_name}</div>
                <div class="title" style="font-size: 13px; font-weight: 600; color: var(--text-main); margin: 2px 0; overflow: hidden; text-overflow: ellipsis; display: -webkit-box; -webkit-line-clamp: 1; -webkit-box-orient: vertical;">${item.title}</div>
                <div class="price" style="font-size: 14px; font-weight: 700; color: var(--text-main);">${formatPrice(item.price)}</div>
            </div>
            <div style="color: var(--glass-border); font-size: 12px;">↗</div>
        </div>
    `).join('');
}

function renderMallCategoryTabs() {
    const container = document.getElementById('mallCategoryTabs');
    if (!container || !dashboardData?.mall_reports?.categories) return;
    const cats = Object.keys(dashboardData.mall_reports.categories).filter(c => c);
    container.innerHTML = cats.map(cat => `<div class="cat-pill ${currentMallCategory === cat ? 'active' : ''}" onclick="selectMallCategory('${cat}')">${cat}</div>`).join('');
    if (!currentMallCategory && cats.length > 0) selectMallCategory(cats[0]);
}

function selectMallCategory(cat) {
    currentMallCategory = cat;
    currentMall = null; // 카테고리 이동 시 셀러 초기화
    selectedMallProductIndex = 0; // 상품 인덱스 초기화
    renderMallCategoryTabs();
    renderMallReportTabs();
}

function renderMallReportTabs() {
    const container = document.getElementById('mall-seller-tabs');
    if (!container || !currentMallCategory) return;
    const malls = Object.keys(dashboardData.mall_reports.categories[currentMallCategory]);
    
    container.innerHTML = malls.map(m => {
        const isActive = currentMall === m;
        const data = dashboardData.mall_reports.categories[currentMallCategory][m];
        let subMenu = '';
        
        if (isActive && data.products) {
            subMenu = `<div class="mall-sub-tabs">` + 
                data.products.map((p, idx) => `
                    <div class="mall-sub-tab ${idx === selectedMallProductIndex ? 'active' : ''}" 
                         onclick="event.stopPropagation(); selectMallProduct(${idx})">
                        ${p.title}
                    </div>
                `).join('') + `</div>`;
        }
        
        return `<div>
            <div class="mall-tab ${isActive ? 'active' : ''}" onclick="selectMall('${m}')">${formatSellerName(m)}</div>
            ${subMenu}
        </div>`;
    }).join('');
    
    if (!currentMall && malls.length > 0) selectMall(malls[0]);
}

function selectMall(m) {
    currentMall = m;
    selectedMallProductIndex = 0; // 셀러 변경 시 상품 인덱스 초기화
    renderMallReportTabs();
    renderMallDashboard(m);
}

function renderMallDashboard(m) {
    const data = dashboardData?.mall_reports?.categories?.[currentMallCategory]?.[m];
    if (!data) return;
    
    const titleEl = document.getElementById('currentMallTitle');
    if (titleEl) titleEl.textContent = formatSellerName(m);
    
    const updateEl = document.getElementById('currentMallUpdate');
    if (updateEl) {
        const lastUpdate = data.last_updated || '';
        const timeStr = lastUpdate ? `${formatKoreanDate(lastUpdate)} (${timeAgo(lastUpdate)})` : '기록 없음 (수집 대기 중)';
        updateEl.innerHTML = `📅 최근 확인일: <span style="font-weight:700; color:var(--text-main)">${timeStr}</span>`;
    }
    
    const infoEl = document.getElementById('currentMallSellerInfo');
    if (infoEl) infoEl.innerText = `${currentMallCategory} 카테고리 매칭 상품 리포트`;
    
    const totalEl = document.getElementById('mallStatTotal');
    if (totalEl) totalEl.innerText = data.total_products || 0;
    
    const decEl = document.getElementById('mallStatDecreased');
    if (decEl) decEl.innerText = data.price_decreased_count || 0;
    
    if (data.products && data.products.length > 0) {
        renderMallChart(data.products);
    }
    renderMallTable(data.products || []);
}

function renderMallChart(prods) {
    const ctx = document.getElementById('mallPriceChart')?.getContext('2d');
    if (!ctx) return;
    if (mallPriceChart) mallPriceChart.destroy();
    const product = prods[selectedMallProductIndex];
    if (!product || !product.history) return;

    const labels = [], data = [];
    product.history.forEach(h => { 
        const d = new Date(h.t);
        labels.push(`${d.getMonth()+1}/${d.getDate()} ${d.getHours()}시`); 
        data.push(h.p); 
    });
    
    mallPriceChart = new Chart(ctx, {
        type: 'line',
        data: { labels, datasets: [{ data, borderColor: '#3182f7', borderWidth: 2, pointRadius: 2, fill: true, backgroundColor: 'rgba(49,130,247,0.1)', tension: 0.4 }] },
        options: { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { x: { display: false }, y: { ticks: { callback: v => v.toLocaleString() + '원' } } } }
    });
}

function renderMallTable(prods) {
    const tbody = document.getElementById('mallTableBody');
    if (!tbody) return;
    const product = prods[selectedMallProductIndex];
    if (!product) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:40px; color:var(--text-muted)">상품을 선택하세요.</td></tr>';
        return;
    }

    const history = product.history || [];
    const sortedHistory = history.slice().reverse();
    
    if (sortedHistory.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" style="text-align:center; padding:40px; color:var(--text-muted)">히스토리 데이터가 없습니다.</td></tr>';
        return;
    }

    tbody.innerHTML = sortedHistory.map((h, idx) => {
        const isRecent = (new Date() - new Date(h.t)) < 3600000;
        
        // 이전 가격 계산 (현재 인덱스 + 1이 있으면 그 가격과 비교)
        const prevH = sortedHistory[idx + 1];
        let diffHtml = '-';
        let prevPriceHtml = '-';
        
        if (prevH && prevH.p) {
            const diff = h.p - prevH.p;
            prevPriceHtml = formatPrice(prevH.p);
            if (diff < 0) {
                diffHtml = `<span style="color:var(--price-down); font-weight:700;">↓ ${formatPrice(Math.abs(diff))}</span>`;
            } else if (diff > 0) {
                diffHtml = `<span style="color:var(--price-up); font-weight:700;">↑ ${formatPrice(diff)}</span>`;
            } else {
                diffHtml = `<span style="color:var(--text-muted);">0</span>`;
            }
        }

        return `
        <tr style="cursor:default" class="${isRecent ? 'recent-data' : ''}">
            <td class="mono">
                <div class="time-ago">${timeAgo(h.t)}</div>
                <div class="time-label">${formatKoreanDate(h.t)}</div>
            </td>
            <td><span class="badge-protection" style="background:rgba(49,130,247,0.05); color:var(--toss-blue); border:none; padding:2px 8px; font-size:11px;">${product.product_code || '-'}</span></td>
            <td>${product.title}</td>
            <td class="mono" style="font-weight:700; color:var(--text-main)">${formatPrice(h.p)}</td>
            <td class="mono" style="color:var(--text-muted); font-size:13px;">${prevPriceHtml}</td>
            <td class="mono">${diffHtml}</td>
            <td style="text-align:right">
                <a href="${product.url}" target="_blank" class="catalog-btn" style="padding: 4px 12px; font-size: 12px;" onclick="event.stopPropagation()">링크</a>
            </td>
        </tr>`;
    }).join('');
}

function selectMallProduct(idx) {
    selectedMallProductIndex = idx;
    const data = dashboardData?.mall_reports?.categories?.[currentMallCategory]?.[currentMall];
    if (!data || !data.products) return;
    
    renderMallReportTabs();
    renderMallChart(data.products);
    renderMallTable(data.products);
}

function goToMallReport(category, mall) {
    switchView('mallReportView');
    selectMallCategory(category);
    selectMall(mall);
    if (window.innerWidth <= 768) toggleSidebar(false);
}

function changeChartDays(days, el) {
    chartDays = days;
    document.querySelectorAll('.filter-pill').forEach(p => p.classList.remove('active'));
    if (el) el.classList.add('active');
    refreshChartView();
}

function formatSellerName(name) {
    if (!name) return '네이버';
    const sellers = {
        '위드모바일': '위드모바일', '쇼마젠시': '쇼마젠시', '코잇': '코잇', '케이원정보': '케이원정보',
        '갤러리몰': '갤러리몰', '맥앤브랜디': '맥앤브랜디', '웰디': '웰디', '프렌드 모바일': '프렌드 모바일',
        '제이플러스시스템': '제이플러스시스템', '신세계몰': '신세계몰', '인터커머스': '인터커머스', '정품포유': '정품포유'
    };
    return sellers[name] || name;
}

function renderPagination() {
    const container = document.getElementById('pagination');
    if (!container) return;
    const totalPages = Math.ceil(currentFilteredHistory.length / itemsPerPageNormal);
    if (totalPages <= 1) { container.innerHTML = ''; return; }
    
    let html = '';
    html += `<button class="page-btn" onclick="changePage(${currentPage - 1})" ${currentPage <= 1 ? 'disabled' : ''}>‹</button>`;
    for (let i = 1; i <= Math.min(totalPages, 10); i++) {
        html += `<button class="page-btn ${currentPage === i ? 'active' : ''}" onclick="changePage(${i})">${i}</button>`;
    }
    if (totalPages > 10) html += `<span style="color:var(--text-muted)">... ${totalPages}</span>`;
    html += `<button class="page-btn" onclick="changePage(${currentPage + 1})" ${currentPage >= totalPages ? 'disabled' : ''}>›</button>`;
    container.innerHTML = html;
}

function changePage(page) {
    const totalPages = Math.ceil(currentFilteredHistory.length / itemsPerPageNormal);
    if (page < 1 || page > totalPages) return;
    currentPage = page;
    renderTable();
}

document.addEventListener('DOMContentLoaded', init);
