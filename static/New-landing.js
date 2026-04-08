/* ═══════════════════════════════════════════
   NEW-LANDING.JS
═══════════════════════════════════════════ */

document.addEventListener('DOMContentLoaded', () => {
    
    // Example: Add an interactive click effect for the Start Planning button
    const startBtn = document.getElementById('startPlanningBtn');
    
    if (startBtn) {
        startBtn.addEventListener('click', (e) => {
            // If you want it to navigate to the upload page, you would update the href in HTML
            // or do it dynamically here:
            // window.location.href = '/upload';
            console.log('Navigating to Upload...');
        });
    }

    // ── HOW IT WORKS TRANSITION LOGIC ──────────────────────
    const heroContent = document.getElementById('hero-content');
    const howItWorksContent = document.getElementById('how-it-works-content');
    const howItWorksBtn = document.getElementById('howItWorksBtn');
    const backToHeroBtn = document.getElementById('backToHeroBtn');

    function swapContent(from, to) {
        // Start fade out
        from.classList.add('out');
        
        // Wait for fade out animation to finish
        setTimeout(() => {
            from.classList.add('hidden');
            from.classList.remove('out');
            
            to.classList.remove('hidden');
            to.classList.add('in');
            
            // Clean up 'in' class after animation
            setTimeout(() => {
                to.classList.remove('in');
            }, 400);
        }, 400);
    }

    if (howItWorksBtn && heroContent && howItWorksContent) {
        howItWorksBtn.addEventListener('click', () => {
            swapContent(heroContent, howItWorksContent);
        });
    }

    if (backToHeroBtn && heroContent && howItWorksContent) {
        backToHeroBtn.addEventListener('click', () => {
            swapContent(howItWorksContent, heroContent);
        });
    }

    // You can initialize your Spline events here once you add the Spline viewer
    const splineContainer = document.getElementById('splineContainer');
    
    if (splineContainer) {
        // Spline code can go here
        // Usually, you embed a <script type="module" src="https://unpkg.com/@splinetool/viewer@1.x.x/build/spline-viewer.js"></script>
        // and put <spline-viewer url="..."></spline-viewer> inside the HTML right side container.
        console.log('Spline container is ready for injection.');
    }

});
