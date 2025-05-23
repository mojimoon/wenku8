document.addEventListener('DOMContentLoaded', function() {

    const searchInput = document.getElementById('searchInput');
    const tableBody = document.getElementById('novelTableBody');
    const tableRows = tableBody.getElementsByTagName('tr');
    const themeToggle = document.getElementById('themeToggle');
    const randomButton = document.getElementById('randomButton');
    const body = document.body;
    
    searchInput.addEventListener('input', function() {
        const searchTerm = searchInput.value.toLowerCase().trim();

        for (let i = 0; i < tableRows.length; i++) {
            const row = tableRows[i];
            const titleCell = row.getElementsByTagName('td')[0];
            const authorCell = row.getElementsByTagName('td')[1];
            if (titleCell.textContent.toLowerCase().includes(searchTerm) || authorCell.textContent.toLowerCase().includes(searchTerm)) {
                row.classList.remove('hidden');
            } else {
                row.classList.add('hidden'); 
            }
        }
    });

    randomButton.addEventListener('click', function() {
        const randomIndex = Math.floor(Math.random() * tableRows.length);
        const randomRow = tableRows[randomIndex];
        const titleCell = randomRow.getElementsByTagName('td')[0];
        const titleText = titleCell.textContent;
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

    const currentTheme = localStorage.getItem('theme');
    if (currentTheme === 'dark') {
        body.classList.add('dark-mode');
    }

    themeToggle.addEventListener('click', function() {
        body.classList.toggle('dark-mode');

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