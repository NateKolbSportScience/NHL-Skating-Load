#### What this app is

Real NHL games replayed on a rink, then GPS-style post-game reports: the kind a sport scientist would hand to coaches and medical staff the morning after a game.

**Game replay:** every shot, hit, faceoff, takeaway, giveaway, block, penalty and goal appears at its real rink location and game time, from the NHL play-by-play. Players come on and off with their real shifts, from the NHL shift charts. Continuous player tracking isn't public, so the puck path and skater positions **between** events are estimated from each player's role, which team has the puck, and where it is. Speed vectors show each skater's estimated direction of travel, coloured by speed band (under 20 km/h, 20–29 km/h, 29+ km/h high-speed). Click a player to follow him. Speeds run from 1× (real time) to 240×, and the 8-bit look can be switched off.

**Skating load:** the NHL publishes real skating data through **NHL EDGE**, but only at the **season level**: distance per 60, speed bursts in three bands, and top speed. For a real game, each of the player's real shifts gets a speed trace calibrated to that profile. A simulated game generates the shifts as well.

#### Real vs estimated

- **Real (NHL API):** play-by-play events with locations and times, shift charts, the box score, and NHL EDGE season profiles (distance per 60 by game state, bursts at 18–20, 20–22 and 22+ mph, top speed, the last 10 games).
- **Estimated:** skater and puck positions between events, the 10 Hz speed traces, speed-zone distances, high-speed distance, accelerations and decelerations. In a simulated game, the shifts are estimated too.

Real numbers carry a green **Real** tag; estimated or simulated ones carry a gold tag.

#### How a game is simulated

1. **Time on ice** is drawn from the player's real average, with his real game-to-game SD from his last 10 games.
2. **Shifts** average about 48 s for forwards and 54 s for D, and are spread across three periods. Each shift is even strength, power play or penalty kill in proportion to the player's real time in each state.
3. **Speed trace:** each shift gets a 10 Hz speed trace. Baseline skating is modelled as a mean-reverting random process, and bursts are added as acceleration–hold–deceleration profiles, with peaks drawn inside EDGE's bands and capped at the player's real top speed.
4. **Calibration:** baseline speed is scaled so each shift's distance matches the player's real distance per 60 for that game state. That rate varies from game to game by the same amount as his real last 10 games, with a small third-period drop-off.
5. **Bursts** are drawn at the player's real per-hour rate in each band, so the simulated burst counts average out to his real season.

Over many simulated games, distance per 60, TOI and burst counts average out to the player's real EDGE numbers.

#### Metrics

| Metric | Definition |
|---|---|
| m/min | Distance per minute on ice (intensity) |
| HSD | High-speed distance: skating at or above 18 mph (29 km/h), EDGE's lowest burst band |
| Sprint distance | Distance at or above 22 mph (35.4 km/h) |
| Bursts | Each excursion above 18 mph, classified by its peak speed into EDGE's bands |
| Accel / Decel | Speed change of at least 2.5 m/s² sustained over 1 s |
| SWC | Smallest worthwhile change: 0.2 × the player's real between-game SD |

#### Data

The NHL's public API (`api-web.nhle.com`) is free and needs no key. Game lists and games load live, so any team, any completed game (including the new season) can be replayed; each game is saved after its first download. EDGE skating profiles are bundled as snapshots and refreshed weekly. Early in a season, a player keeps his previous-season profile until he has 10+ games.

NHL EDGE data © NHL. This is an independent portfolio project, not affiliated with or endorsed by the NHL.

Built by **Nate Kolb**, MSc, RSCC, CPSS · [GitHub](https://github.com/NateKolbSportScience) · [LinkedIn](https://www.linkedin.com/in/nathan-kolb-45a9a6215)
