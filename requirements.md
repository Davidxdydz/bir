# Beerpong Website


## Features

- Team Registration: Team name, team password. No team name can be used more than once. No email verification.
- Login: Team name, team password.
- Leaderboard: Show teams, highlight logged in team. Team name, elo, wins, losses, plays.
- Rules page: Show rules.
- Notifications: ask to show notifications, show notifications.
- logout button

## Layout:
- Without and without login: start on leaderboard.
- Rules page: Show rules.
- schedule page: show schedule for each of the n tables.

## Flows:
- When logged in, "find match" button. -> pending
- toggle for available for matchmaking.
- Notify when match is found
- Notify when match is about to start
- when match starts, show match running page.
- when match ends, show page to input results (how many cups you shot, how many the other team shot). when matching is confirmed, update leaderboard.

##
- persistent database for matches, teams and schedule.