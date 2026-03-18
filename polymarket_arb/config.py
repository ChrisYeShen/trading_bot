"""
config.py — 全局配置、城市坐标表、运动/联赛映射
"""
import os
from dataclasses import dataclass, field
from dotenv import load_dotenv

load_dotenv()


@dataclass
class BotConfig:
    # Polymarket
    private_key: str
    host: str

    # The Odds API
    odds_api_key: str

    # 策略参数
    min_edge: float
    max_order_size: float
    max_open_orders: int
    scan_interval: int
    dry_run: bool

    @classmethod
    def from_env(cls) -> "BotConfig":
        return cls(
            private_key=os.environ["POLY_PRIVATE_KEY"],
            host=os.getenv("POLY_HOST", "https://clob.polymarket.com"),
            odds_api_key=os.getenv("ODDS_API_KEY", ""),
            min_edge=float(os.getenv("MIN_EDGE", "0.08")),
            max_order_size=float(os.getenv("MAX_ORDER_SIZE", "50")),
            max_open_orders=int(os.getenv("MAX_OPEN_ORDERS", "10")),
            scan_interval=int(os.getenv("SCAN_INTERVAL", "300")),
            dry_run=os.getenv("DRY_RUN", "true").lower() == "true",
        )


# ── 美国城市 → (纬度, 经度) ───────────────────────────────
CITY_COORDS: dict[str, tuple[float, float]] = {
    "new york":    (40.7128, -74.0060),
    "nyc":         (40.7128, -74.0060),
    "los angeles": (34.0522, -118.2437),
    "la":          (34.0522, -118.2437),
    "chicago":     (41.8781, -87.6298),
    "houston":     (29.7604, -95.3698),
    "phoenix":     (33.4484, -112.0740),
    "philadelphia":(39.9526, -75.1652),
    "san antonio": (29.4241, -98.4936),
    "san diego":   (32.7157, -117.1611),
    "dallas":      (32.7767, -96.7970),
    "san jose":    (37.3382, -121.8863),
    "austin":      (30.2672, -97.7431),
    "seattle":     (47.6062, -122.3321),
    "denver":      (39.7392, -104.9903),
    "miami":       (25.7617, -80.1918),
    "atlanta":     (33.7490, -84.3880),
    "boston":      (42.3601, -71.0589),
    "las vegas":   (36.1699, -115.1398),
    "portland":    (45.5051, -122.6750),
    "minneapolis": (44.9778, -93.2650),
    "detroit":     (42.3314, -83.0458),
    "nashville":   (36.1627, -86.7816),
    "memphis":     (35.1495, -90.0490),
    "louisville":  (38.2527, -85.7585),
    "baltimore":   (39.2904, -76.6122),
    "milwaukee":   (43.0389, -87.9065),
    "kansas city": (39.0997, -94.5786),
    "raleigh":     (35.7796, -78.6382),
    "omaha":       (41.2565, -95.9345),
    "new orleans": (29.9511, -90.0715),
    "cleveland":   (41.4993, -81.6944),
    "pittsburgh":  (40.4406, -79.9959),
    "tampa":       (27.9506, -82.4572),
    "cincinnati":  (39.1031, -84.5120),
    "buffalo":     (42.8864, -78.8784),
    "salt lake city": (40.7608, -111.8910),
    "indianapolis": (39.7684, -86.1581),
    "charlotte":   (35.2271, -80.8431),
}

# ── The Odds API スポーツキー（单场赔率）────────────────────
# https://the-odds-api.com/sports-odds-data/sports-apis.html
ODDS_API_SPORT_KEYS: dict[str, str] = {
    "nfl":          "americanfootball_nfl",
    "nfl_preseason":"americanfootball_nfl_preseason",
    "nba":          "basketball_nba",
    "ncaab":        "basketball_ncaab",
    "epl":          "soccer_epl",
    "ucl":          "soccer_uefa_champs_league",
    "la_liga":      "soccer_spain_la_liga",
    "bundesliga":   "soccer_germany_bundesliga",
    "serie_a":      "soccer_italy_serie_a",
    "ligue_1":      "soccer_france_ligue_one",
    "mls":          "soccer_usa_mls",
}

# ── The Odds API 冠军/晋级 Futures 赔率 ──────────────────────
FUTURES_SPORT_KEYS: dict[str, str] = {
    "nba":       "basketball_nba_championship_winner",
    "nfl":       "americanfootball_nfl_super_bowl_winner",
    "ucl":       "soccer_uefa_champs_league_winner",
    "world_cup": "soccer_fifa_world_cup_winner",
    "epl":       "soccer_epl_winner",
    "la_liga":   "soccer_spain_la_liga_winner",
    "bundesliga":"soccer_germany_bundesliga_winner",
}

# ── NBA / NFL / Soccer 球队别名 → 官方全名（用于模糊匹配）────
TEAM_ALIASES: dict[str, str] = {
    # NBA
    "lakers":         "Los Angeles Lakers",
    "celtics":        "Boston Celtics",
    "warriors":       "Golden State Warriors",
    "bucks":          "Milwaukee Bucks",
    "nets":           "Brooklyn Nets",
    "heat":           "Miami Heat",
    "nuggets":        "Denver Nuggets",
    "suns":           "Phoenix Suns",
    "clippers":       "Los Angeles Clippers",
    "knicks":         "New York Knicks",
    "bulls":          "Chicago Bulls",
    "mavericks":      "Dallas Mavericks",
    "76ers":          "Philadelphia 76ers",
    "raptors":        "Toronto Raptors",
    "jazz":           "Utah Jazz",
    "hornets":        "Charlotte Hornets",
    "hawks":          "Atlanta Hawks",
    "thunder":        "Oklahoma City Thunder",
    "rockets":        "Houston Rockets",
    "cavaliers":      "Cleveland Cavaliers",
    "magic":          "Orlando Magic",
    "grizzlies":      "Memphis Grizzlies",
    "spurs":          "San Antonio Spurs",
    "trail blazers":  "Portland Trail Blazers",
    "blazers":        "Portland Trail Blazers",
    "pistons":        "Detroit Pistons",
    "pelicans":       "New Orleans Pelicans",
    "timberwolves":   "Minnesota Timberwolves",
    "wolves":         "Minnesota Timberwolves",
    "pacers":         "Indiana Pacers",
    "kings":          "Sacramento Kings",
    "wizards":        "Washington Wizards",
    # NFL
    "chiefs":         "Kansas City Chiefs",
    "patriots":       "New England Patriots",
    "eagles":         "Philadelphia Eagles",
    "49ers":          "San Francisco 49ers",
    "cowboys":        "Dallas Cowboys",
    "packers":        "Green Bay Packers",
    "ravens":         "Baltimore Ravens",
    "bills":          "Buffalo Bills",
    "bengals":        "Cincinnati Bengals",
    "rams":           "Los Angeles Rams",
    # Soccer — countries
    "italy":          "Italy",
    "france":         "France",
    "england":        "England",
    "germany":        "Germany",
    "spain":          "Spain",
    "brazil":         "Brazil",
    "argentina":      "Argentina",
    "portugal":       "Portugal",
    "netherlands":    "Netherlands",
    "ukraine":        "Ukraine",
    "poland":         "Poland",
    "sweden":         "Sweden",
    # Soccer — clubs
    "arsenal":        "Arsenal",
    "chelsea":        "Chelsea",
    "liverpool":      "Liverpool",
    "manchester city":"Manchester City",
    "man city":       "Manchester City",
    "manchester united":"Manchester United",
    "man united":     "Manchester United",
    "real madrid":    "Real Madrid",
    "barcelona":      "FC Barcelona",
    "psg":            "Paris Saint-Germain",
    "juventus":       "Juventus",
    "bayern":         "Bayern Munich",
    "inter milan":    "Inter Milan",
    "ac milan":       "AC Milan",
    "atletico madrid":"Atletico Madrid",
    "dortmund":       "Borussia Dortmund",
}

# Polymarket 市场关键词 → 运动类别（用于 market_scanner 分类）
SPORT_KEYWORDS: dict[str, list[str]] = {
    "nfl":     ["nfl", "super bowl", "chiefs", "patriots", "eagles", "49ers",
                "cowboys", "packers", "ravens", "bills", "bengals", "rams"],
    "nba":     ["nba", "lakers", "celtics", "warriors", "bucks", "nets",
                "heat", "nuggets", "suns", "clippers", "knicks", "bulls"],
    "soccer":  ["premier league", "champions league", "la liga", "bundesliga",
                "serie a", "ligue 1", "mls", "euro", "world cup", "copa",
                "arsenal", "chelsea", "liverpool", "manchester", "real madrid",
                "barcelona", "psg", "juventus", "bayern", "inter milan"],
}

# 天气事件关键词（用于 market_scanner 分类）
WEATHER_KEYWORDS = [
    "rain", "snow", "temperature", "fahrenheit", "celsius",
    "hurricane", "tornado", "blizzard", "precipitation",
    "weather", "forecast", "storm", "flood", "heat wave",
    "high temp", "low temp", "freeze",
]
