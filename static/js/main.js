document.addEventListener('DOMContentLoaded', () => {
    const toggleStatusBtn = document.getElementById('toggle-status-btn');
    const statusIndicator = document.getElementById('status-indicator');
    const statusMessage = document.getElementById('status-message');

    // Request notification permission
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }

    // Polling interval (1 second)
    const POLL_INTERVAL = 1000;

    if (toggleStatusBtn) {
        toggleStatusBtn.addEventListener('click', async () => {
            try {
                const response = await fetch('/api/toggle_status', { method: 'POST' });
                const data = await response.json();

                if (data.status === 'match_found') {
                    window.location.href = `/match/${data.match_id}`;
                } else if (data.status === 'error') {
                    // User already in match or other error
                    console.log(data.message);
                }
            } catch (error) {
                console.error('Error toggling status:', error);
            }
        });
    }

    // No need to update UI locally - server-side rendering handles initial state
    // Polling will keep it in sync

    // Global Status Polling
    // Only poll if we are logged in (heuristic: toggle button exists)

    // Global status polling
    if (statusIndicator) {
        let countdownInterval = null;

        // Check status immediately on page load
        checkStatus();

        // Then poll every second
        setInterval(checkStatus, POLL_INTERVAL);

        async function checkStatus() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                const matchCard = document.getElementById('match-card');
                const matchmakingArea = document.getElementById('matchmaking-area');
                const goToMatchBtn = document.getElementById('go-to-match-btn');

                // Safety check - ensure elements exist
                if (!matchCard || !matchmakingArea) return;

                if (data.status === 'in_match') {
                    // Always redirect to match page if we are on index
                    if (window.location.pathname === '/' || window.location.pathname === '/index') {
                        window.location.href = `/match/${data.match_id}`;
                        return;
                    }

                    // Show match card, hide matchmaking
                    matchCard.classList.remove('hidden');
                    matchmakingArea.classList.add('hidden');                    // Get opponent name
                    const opponentName = data.is_team1 ? data.team2_name : data.team1_name;
                    const opponentNameEl = document.getElementById('opponent-name');
                    if (opponentNameEl) opponentNameEl.textContent = opponentName;

                    // Set up go to match button
                    if (goToMatchBtn) goToMatchBtn.href = `/match/${data.match_id}`;

                    // Simplified UI - no countdown here anymore
                    if (countdownInterval) clearInterval(countdownInterval);

                    // We don't need to handle specific states here anymore because we redirect to match page
                    // But we keep the card visible just in case redirect fails or user navigates back
                } else {
                    // Hide match card, show matchmaking
                    matchCard.classList.add('hidden');
                    matchmakingArea.classList.remove('hidden');
                    if (countdownInterval) clearInterval(countdownInterval);

                    // Update availability UI
                    const toggleBtn = document.getElementById('toggle-status-btn');
                    const statusIndicator = document.getElementById('status-indicator');

                    if (toggleBtn && statusIndicator) {
                        if (data.status === 'available') {
                            toggleBtn.textContent = 'Cancel Search';
                            toggleBtn.classList.remove('bg-gradient-to-r', 'from-pink-600', 'to-violet-600', 'shadow-pink-500/20');
                            toggleBtn.classList.add('bg-slate-600', 'text-white', 'hover:bg-slate-500');
                            statusIndicator.textContent = 'Searching for opponent...';
                        } else {
                            toggleBtn.textContent = 'Find Match';
                            toggleBtn.classList.add('bg-gradient-to-r', 'from-pink-600', 'to-violet-600', 'shadow-pink-500/20');
                            toggleBtn.classList.remove('bg-slate-600', 'text-white', 'hover:bg-slate-500');
                            statusIndicator.textContent = 'Click to join the matchmaking queue';
                        }
                    }
                }
            } catch (error) {
                console.error('Error polling status:', error);
            }
        }

        // Notification Polling - check every minute for upcoming matches
        setInterval(async () => {
            try {
                const response = await fetch('/api/check_notifications');
                const data = await response.json();

                if (data.notify && Notification.permission === 'granted') {
                    new Notification('Match Starting Soon!', {
                        body: `Your match starts in ${data.minutes_until} minutes. Get ready!`,
                        icon: '/static/logo.png',
                        tag: 'match-reminder'
                    });
                }
            } catch (error) {
                console.error('Error checking notifications:', error);
            }
        }, 60000); // Check every minute
    }
});
