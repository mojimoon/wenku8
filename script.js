document.addEventListener('DOMContentLoaded', function() {

    const searchInput = document.getElementById('searchInput');
    const tableBody = document.getElementById('novelTableBody');
    const tableRows = tableBody.getElementsByTagName('tr');
    const themeToggle = document.getElementById('themeToggle');
    const randomButton = document.getElementById('randomButton');
    const clearInput = document.getElementById('clearInput');
    const body = document.body;

    // --- 搜索功能 ---
    searchInput.addEventListener('input', function() {
        const searchTerm = searchInput.value.toLowerCase().trim();

        for (let i = 0; i < tableRows.length; i++) {
            const row = tableRows[i];
            const titleCell = row.getElementsByTagName('td')[0];
            if (titleCell) {
                // const titleText = titleCell.textContent || titleCell.innerText;
                const titleText = titleCell.textContent;
                if (titleText.toLowerCase().includes(searchTerm)) {
                    row.classList.remove('hidden');
                } else {
                    row.classList.add('hidden'); 
                }
            }
        }
    });

    randomButton.addEventListener('click', function() {
        const randomIndex = Math.floor(Math.random() * tableRows.length);
        const randomRow = tableRows[randomIndex];
        const aTag = randomRow.getElementsByTagName('a')[0];
        // const titleText = aTag.textContent || aTag.innerText;
        const titleText = aTag.textContent;
        searchInput.value = titleText;
        for (let i = 0; i < tableRows.length; i++) {
            const row = tableRows[i];
            if (row === randomRow) {
                row.classList.remove('hidden');
            } else {
                row.classList.add('hidden'); 
            }
        }
    });

    // --- 日间/夜间模式切换 ---
    const currentTheme = localStorage.getItem('theme');
    if (currentTheme === 'dark') {
        body.classList.add('dark-mode');
    }

    themeToggle.addEventListener('click', function() {
        body.classList.toggle('dark-mode');

        // 将主题偏好保存到本地存储
        if (body.classList.contains('dark-mode')) {
            localStorage.setItem('theme', 'dark');
        } else {
            localStorage.setItem('theme', 'light');
        }
    });

    clearInput.addEventListener('click', function() {
        searchInput.value = '';
        for (let i = 0; i < tableRows.length; i++) {
            const row = tableRows[i];
            row.classList.remove('hidden'); 
        }
    });
});