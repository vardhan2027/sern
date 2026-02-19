// SERN - Smart Emergency Resource Network
// Main JavaScript file

document.addEventListener('DOMContentLoaded', function() {
    // Auto-dismiss flash messages after 5 seconds
    const flashMessages = document.querySelectorAll('.flash');
    flashMessages.forEach(function(flash) {
        setTimeout(function() {
            flash.style.opacity = '0';
            flash.style.transition = 'opacity 0.5s';
            setTimeout(function() {
                flash.remove();
            }, 500);
        }, 5000);
    });

    // Confirm before declining a request
    const declineButtons = document.querySelectorAll('button[value="decline"]');
    declineButtons.forEach(function(btn) {
        btn.addEventListener('click', function(e) {
            if (!confirm('Are you sure you cannot help with this emergency? This may affect your IRI score.')) {
                e.preventDefault();
            }
        });
    });

    // Highlight urgent requests
    const urgentCards = document.querySelectorAll('.urgency-border-critical');
    urgentCards.forEach(function(card) {
        card.style.animation = 'pulse 2s infinite';
    });

    // Add pulse animation
    const style = document.createElement('style');
    style.textContent = `
        @keyframes pulse {
            0%, 100% { box-shadow: 0 0 0 0 rgba(220, 38, 38, 0.4); }
            50% { box-shadow: 0 0 0 10px rgba(220, 38, 38, 0); }
        }
    `;
    document.head.appendChild(style);
});

// Register service worker for PWA
if ('serviceWorker' in navigator) {
    window.addEventListener('load', function() {
        navigator.serviceWorker.register('/sw.js')
            .then(function(registration) {
                console.log('ServiceWorker registration successful with scope: ', registration.scope);
            }, function(err) {
                console.log('ServiceWorker registration failed: ', err);
            });
    });
}

// API helper for availability toggle
async function toggleAvailability() {
    try {
        const response = await fetch('/api/availability', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            }
        });
        const data = await response.json();
        return data.available;
    } catch (error) {
        console.error('Error toggling availability:', error);
        return null;
    }
}

// Format relative time
function formatTimeAgo(dateString) {
    const date = new Date(dateString);
    const now = new Date();
    const seconds = Math.floor((now - date) / 1000);
    
    if (seconds < 60) return 'Just now';
    if (seconds < 3600) return Math.floor(seconds / 60) + ' min ago';
    if (seconds < 86400) return Math.floor(seconds / 3600) + ' hours ago';
    return Math.floor(seconds / 86400) + ' days ago';
}
