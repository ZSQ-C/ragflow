const CATEGORIES = {
    expense: ['餐饮', '交通', '购物', '娱乐', '医疗', '教育', '住房', '通讯', '其他'],
    income: ['工资', '奖金', '投资', '兼职', '其他']
};

let mainChart = null;
let categoryChart = null;

function getRecords() {
    const data = localStorage.getItem('accounting_records');
    return data ? JSON.parse(data) : [];
}

function saveRecords(records) {
    localStorage.setItem('accounting_records', JSON.stringify(records));
}

function renderCategories(type) {
    const categorySelect = document.getElementById('category');
    categorySelect.innerHTML = '';
    CATEGORIES[type].forEach(cat => {
        const option = document.createElement('option');
        option.value = cat;
        option.textContent = cat;
        categorySelect.appendChild(option);
    });
}

function formatDate(dateStr) {
    const date = new Date(dateStr);
    return `${date.getMonth() + 1}月${date.getDate()}日`;
}

function formatAmount(amount) {
    return parseFloat(amount).toFixed(2);
}

function renderRecords() {
    const recordsList = document.getElementById('recordsList');
    const records = getRecords();
    recordsList.innerHTML = '';
    
    if (records.length === 0) {
        recordsList.innerHTML = '<div class="text-center text-gray-400 py-8">暂无记录</div>';
        return;
    }
    
    records.slice().reverse().forEach(record => {
        const div = document.createElement('div');
        div.className = 'flex items-center justify-between p-3 bg-gray-50 rounded-lg hover:bg-gray-100 transition-colors';
        
        const isExpense = record.type === 'expense';
        const amountClass = isExpense ? 'text-red-600' : 'text-green-600';
        const amountSign = isExpense ? '-' : '+';
        
        div.innerHTML = `
            <div class="flex items-center space-x-3">
                <div class="w-10 h-10 rounded-full flex items-center justify-center text-white text-lg" style="background-color: ${isExpense ? '#ef4444' : '#22c55e'}">
                    ${isExpense ? '💸' : '💰'}
                </div>
                <div>
                    <div class="font-medium text-gray-800">${record.category}</div>
                    <div class="text-sm text-gray-500">${formatDate(record.date)} ${record.note ? '- ' + record.note : ''}</div>
                </div>
            </div>
            <div class="flex items-center space-x-3">
                <div class="font-bold ${amountClass}">${amountSign}¥${formatAmount(record.amount)}</div>
                <button onclick="deleteRecord('${record.id}')" class="text-gray-400 hover:text-red-500">
                    <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path>
                    </svg>
                </button>
            </div>
        `;
        
        recordsList.appendChild(div);
    });
}

function deleteRecord(id) {
    if (confirm('确定要删除这条记录吗？')) {
        const records = getRecords().filter(r => r.id !== id);
        saveRecords(records);
        renderRecords();
        updateStats();
        updateCharts();
    }
}

function updateStats() {
    const records = getRecords();
    const now = new Date();
    const currentMonth = now.getMonth();
    const currentYear = now.getFullYear();
    
    let income = 0;
    let expense = 0;
    
    records.forEach(record => {
        const date = new Date(record.date);
        if (date.getMonth() === currentMonth && date.getFullYear() === currentYear) {
            const amount = parseFloat(record.amount);
            if (record.type === 'income') {
                income += amount;
            } else {
                expense += amount;
            }
        }
    });
    
    document.getElementById('monthIncome').textContent = formatAmount(income);
    document.getElementById('monthExpense').textContent = formatAmount(expense);
    document.getElementById('monthBalance').textContent = formatAmount(income - expense);
}

function updateCharts() {
    const records = getRecords();
    const period = document.getElementById('chartPeriod').value;
    const now = new Date();
    
    let labels = [];
    let incomeData = [];
    let expenseData = [];
    
    if (period === 'month') {
        for (let i = 30; i >= 0; i--) {
            const date = new Date(now);
            date.setDate(date.getDate() - i);
            labels.push(`${date.getMonth() + 1}/${date.getDate()}`);
            
            let dayIncome = 0;
            let dayExpense = 0;
            
            records.forEach(record => {
                const rDate = new Date(record.date);
                if (rDate.toDateString() === date.toDateString()) {
                    const amount = parseFloat(record.amount);
                    if (record.type === 'income') {
                        dayIncome += amount;
                    } else {
                        dayExpense += amount;
                    }
                }
            });
            
            incomeData.push(dayIncome);
            expenseData.push(dayExpense);
        }
    } else {
        for (let i = 11; i >= 0; i--) {
            const date = new Date(now.getFullYear(), now.getMonth() - i, 1);
            labels.push(`${date.getMonth() + 1}月`);
            
            let monthIncome = 0;
            let monthExpense = 0;
            
            records.forEach(record => {
                const rDate = new Date(record.date);
                if (rDate.getMonth() === date.getMonth() && rDate.getFullYear() === date.getFullYear()) {
                    const amount = parseFloat(record.amount);
                    if (record.type === 'income') {
                        monthIncome += amount;
                    } else {
                        monthExpense += amount;
                    }
                }
            });
            
            incomeData.push(monthIncome);
            expenseData.push(monthExpense);
        }
    }
    
    if (mainChart) {
        mainChart.destroy();
    }
    
    const ctx = document.getElementById('mainChart').getContext('2d');
    mainChart = new Chart(ctx, {
        type: 'line',
        data: {
            labels: labels,
            datasets: [
                {
                    label: '收入',
                    data: incomeData,
                    borderColor: '#22c55e',
                    backgroundColor: 'rgba(34, 197, 94, 0.1)',
                    fill: true,
                    tension: 0.4
                },
                {
                    label: '支出',
                    data: expenseData,
                    borderColor: '#ef4444',
                    backgroundColor: 'rgba(239, 68, 68, 0.1)',
                    fill: true,
                    tension: 0.4
                }
            ]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom'
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        callback: value => '¥' + value
                    }
                }
            }
        }
    });
    
    const currentMonthRecords = records.filter(r => {
        const date = new Date(r.date);
        return date.getMonth() === now.getMonth() && date.getFullYear() === now.getFullYear() && r.type === 'expense';
    });
    
    const categoryStats = {};
    currentMonthRecords.forEach(r => {
        categoryStats[r.category] = (categoryStats[r.category] || 0) + parseFloat(r.amount);
    });
    
    const sortedCategories = Object.entries(categoryStats).sort((a, b) => b[1] - a[1]).slice(0, 5);
    
    if (categoryChart) {
        categoryChart.destroy();
    }
    
    const catCtx = document.getElementById('categoryChart').getContext('2d');
    categoryChart = new Chart(catCtx, {
        type: 'doughnut',
        data: {
            labels: sortedCategories.map(c => c[0]),
            datasets: [{
                data: sortedCategories.map(c => c[1]),
                backgroundColor: [
                    '#ef4444', '#f97316', '#eab308', '#22c55e', '#3b82f6'
                ],
                borderWidth: 0
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'right',
                    labels: {
                        boxWidth: 12,
                        padding: 10
                    }
                }
            }
        }
    });
}

document.getElementById('type').addEventListener('change', (e) => {
    renderCategories(e.target.value);
});

document.getElementById('addForm').addEventListener('submit', (e) => {
    e.preventDefault();
    
    const record = {
        id: Date.now().toString(),
        type: document.getElementById('type').value,
        category: document.getElementById('category').value,
        amount: document.getElementById('amount').value,
        date: document.getElementById('date').value,
        note: document.getElementById('note').value
    };
    
    const records = getRecords();
    records.push(record);
    saveRecords(records);
    
    document.getElementById('addForm').reset();
    document.getElementById('date').valueAsDate = new Date();
    
    renderRecords();
    updateStats();
    updateCharts();
});

document.getElementById('clearAll').addEventListener('click', () => {
    if (confirm('确定要清空所有数据吗？此操作不可恢复！')) {
        localStorage.removeItem('accounting_records');
        renderRecords();
        updateStats();
        updateCharts();
    }
});

document.getElementById('chartPeriod').addEventListener('change', updateCharts);

document.addEventListener('DOMContentLoaded', () => {
    const today = new Date().toISOString().split('T')[0];
    document.getElementById('date').value = today;
    renderCategories('expense');
    renderRecords();
    updateStats();
    updateCharts();
});