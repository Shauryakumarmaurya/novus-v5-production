function openSectionModal(title, htmlContent) {
            document.getElementById('section-modal-title-text').textContent = title;
            document.getElementById('section-modal-body').innerHTML = htmlContent;
            document.getElementById('section-modal-backdrop').classList.add('open');
            document.body.style.overflow = 'hidden';
        }
        function closeSectionModal() {
            document.getElementById('section-modal-backdrop').classList.remove('open');
            document.body.style.overflow = '';
        }
        document.getElementById('section-modal-close-btn').addEventListener('click', closeSectionModal);
        document.getElementById('section-modal-backdrop').addEventListener('click', function(e) {
            if (e.target === this) closeSectionModal();
        });
        document.addEventListener('keydown', function(e) {
            if (e.key === 'Escape') closeSectionModal();
        });