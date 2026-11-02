// Chart and stats helpers shared by stocks.html and portfolio.html

// Draw a stock's price history, or update the chart already on this canvas
function drawChart(canvas, history, up) {
    // UTC from the server -> your local time
    const labels = history.map(p => new Date(p.time).toLocaleString('en-US',
        {month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit'}
    ));
    const prices = history.map(p => parseFloat(p.price));

    // Green and Red from Bootstrap 
    const color = up ? '#198754' : '#dc3545';


    // Chart.js remembers the chart on each canvas, so we can reuse it if it exists
    const chart = Chart.getChart(canvas);
    if (chart) {
        chart.data.labels = labels;
        chart.data.datasets[0].data = prices;
        chart.data.datasets[0].borderColor = color;
        chart.update('none'); // 'none' disables the animation
        return;
    }

    new Chart(canvas, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [{
                data: prices,
                borderColor: color,
                borderWidth: 2,
                pointRadius: 0,
                tension: 0.4
            }],
        },
        options: {
            maintainAspectRatio: false,
            interaction: { mode: 'index', intersect: false },
            plugins: { legend: {display: false}},
            scales: {
                x: { ticks: {maxTicksLimit: 6}},
                y: { ticks: {callback: value => '$' + value}},
            },
        },
    });
}

// Fill in the stock stats table and chart inside a dropdown or popup
function showStats(container, data) {
    // The plain dollar amounts
    for (const key of ['initial_price', 'all_time_high', 'all_time_low', 'week_high', 'week_low']) {
        container.querySelector(`[data-stat="${key}"]`).textContent = '$' + data[key];
    }

    // Change since launch: green and + if up, red and - if down
    const change = container.querySelector('[data-stat="change"]');
    const up = parseFloat(data.change) >= 0;
    const sign = up ? '+' : '-';
    change.textContent = sign + '$' + data.change.replace('-', '') + ' (' + sign + data.change_pct.replace('-', '') + '%)';
    change.className = 'text-end ' + (up ? 'text-success' : 'text-danger');

    // Big numbers with commas, like 121,400,000
    container.querySelector('[data-stat="market_cap"]').textContent =
        '$' + Number(data.market_cap).toLocaleString('en-US', {maximumFractionDigits: 0});
    container.querySelector('[data-stat="available_shares"]').textContent =
        data.available_shares.toLocaleString('en-US') + ' / ' + data.shares_outstanding.toLocaleString('en-US');

    drawChart(container.querySelector('canvas'), data.history, up);
}

// Get a stock's stats without moving its price, then show them in a popup
async function loadStats(url, container) {
    const response = await fetch(url);
    showStats(container, await response.json());
}