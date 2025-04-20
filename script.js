document.addEventListener('DOMContentLoaded', function() {

    const searchInput = document.getElementById('searchInput');
    const tableBody = document.getElementById('novelTableBody');
    const tableRows = tableBody.getElementsByTagName('tr');
    const themeToggle = document.getElementById('themeToggle');
    const body = document.body;

    // --- 搜索功能 ---
    searchInput.addEventListener('input', function() {
        const searchTerm = searchInput.value.toLowerCase().trim();

        for (let i = 0; i < tableRows.length; i++) {
            const row = tableRows[i];
            // 获取第一列（小说标题列）的文本内容
            const titleCell = row.getElementsByTagName('td')[0];
            if (titleCell) {
                const titleText = titleCell.textContent || titleCell.innerText;
                // 检查标题是否包含搜索词
                if (titleText.toLowerCase().includes(searchTerm)) {
                    row.classList.remove('hidden'); // 显示匹配行
                } else {
                    row.classList.add('hidden'); // 隐藏不匹配行
                }
            }
        }
    });

    // --- 日间/夜间模式切换 ---
    // 检查本地存储中是否有主题偏好
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

});