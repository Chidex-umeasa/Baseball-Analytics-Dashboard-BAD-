import uuid
import json
from datetime import datetime, date
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Dict, Any, List, Optional

from backend.database import get_db
from backend.models.game_record import GameRecord
from backend.models.game_session import GameSession

router = APIRouter()

# In-memory cache for active sessions (warmed from DB on demand)
_game_sessions: Dict[str, Dict[str, Any]] = {}


class StartGameBody(BaseModel):
    home_team: str
    away_team: str
    game_date: str


class AtBatBody(BaseModel):
    game_id: str
    player_name: str
    team: str
    result: str  # single, double, triple, hr, walk, strikeout, out


class PitchBody(BaseModel):
    game_id: str
    pitcher_name: str
    team: str
    result: str  # strikeout, ball, hit, walk


def _persist_session(db: Session, session: Dict[str, Any]) -> None:
    """Upsert session state into the database."""
    game_id = session["game_id"]
    row = db.query(GameSession).filter(GameSession.game_id == game_id).first()
    session_json = json.dumps(session)
    if row:
        row.session_data = session_json
        row.saved = session["saved"]
        row.updated_at = datetime.utcnow()
    else:
        db.add(GameSession(
            game_id=game_id,
            home_team=session["home_team"],
            away_team=session["away_team"],
            game_date=session["game_date"],
            session_data=session_json,
            saved=session["saved"],
        ))
    db.commit()


def _load_session(db: Session, game_id: str) -> Optional[Dict[str, Any]]:
    """Return session from memory cache, falling back to DB on cache miss."""
    if game_id in _game_sessions:
        return _game_sessions[game_id]
    row = db.query(GameSession).filter(GameSession.game_id == game_id).first()
    if row:
        session = json.loads(row.session_data)
        _game_sessions[game_id] = session
        return session
    return None


@router.get("/game-mode/sessions")
def list_sessions(db: Session = Depends(get_db)) -> List[Dict[str, Any]]:
    """List all in-progress (not yet saved) game sessions."""
    rows = (
        db.query(GameSession)
        .filter(GameSession.saved == False)  # noqa: E712
        .order_by(GameSession.created_at.desc())
        .all()
    )
    return [
        {
            "game_id": r.game_id,
            "home_team": r.home_team,
            "away_team": r.away_team,
            "game_date": r.game_date,
            "saved": r.saved,
            "created_at": str(r.created_at),
        }
        for r in rows
    ]


@router.post("/game-mode/start")
def start_game(body: StartGameBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    game_id = f"GM-{uuid.uuid4().hex[:8].upper()}"
    session: Dict[str, Any] = {
        "game_id": game_id,
        "home_team": body.home_team,
        "away_team": body.away_team,
        "game_date": body.game_date,
        "inning": 1,
        "score": {body.home_team: 0, body.away_team: 0},
        "at_bats": [],
        "pitches": [],
        "player_stats": {},
        "pitcher_stats": {},
        "saved": False,
    }
    _game_sessions[game_id] = session
    _persist_session(db, session)
    return {"game_id": game_id, "message": "Game session started", "session": session}


@router.post("/game-mode/at-bat")
def log_at_bat(body: AtBatBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    session = _load_session(db, body.game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Game session not found.")

    result = body.result.lower()
    key = f"{body.player_name}|{body.team}"
    if key not in session["player_stats"]:
        session["player_stats"][key] = {
            "player_name": body.player_name,
            "team": body.team,
            "at_bats": 0,
            "hits": 0,
            "doubles": 0,
            "triples": 0,
            "home_runs": 0,
            "runs": 0,
            "rbi": 0,
            "walks": 0,
            "strikeouts": 0,
        }

    stats = session["player_stats"][key]

    if result in ("single", "double", "triple", "hr"):
        stats["at_bats"] += 1
        stats["hits"] += 1
        if result == "double":
            stats["doubles"] += 1
        elif result == "triple":
            stats["triples"] += 1
        elif result == "hr":
            stats["home_runs"] += 1
            stats["runs"] += 1
            if body.team in session["score"]:
                session["score"][body.team] += 1
    elif result == "walk":
        stats["walks"] += 1
    elif result == "strikeout":
        stats["at_bats"] += 1
        stats["strikeouts"] += 1
    elif result == "out":
        stats["at_bats"] += 1

    session["at_bats"].append({"player": body.player_name, "team": body.team, "result": result})
    _persist_session(db, session)

    return {"message": "At-bat logged", "player_stats": stats, "score": session["score"]}


@router.post("/game-mode/pitch")
def log_pitch(body: PitchBody, db: Session = Depends(get_db)) -> Dict[str, Any]:
    session = _load_session(db, body.game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Game session not found.")

    result = body.result.lower()
    key = f"{body.pitcher_name}|{body.team}"
    if key not in session["pitcher_stats"]:
        session["pitcher_stats"][key] = {
            "pitcher_name": body.pitcher_name,
            "team": body.team,
            "innings_pitched": 0.0,
            "strikeouts_pitched": 0,
            "walks_allowed": 0,
            "hits_allowed": 0,
            "earned_runs": 0,
        }

    stats = session["pitcher_stats"][key]

    if result == "strikeout":
        stats["strikeouts_pitched"] += 1
        stats["innings_pitched"] = round(stats["innings_pitched"] + 1 / 3, 2)
    elif result == "walk":
        stats["walks_allowed"] += 1
    elif result == "hit":
        stats["hits_allowed"] += 1

    session["pitches"].append({"pitcher": body.pitcher_name, "team": body.team, "result": result})
    _persist_session(db, session)

    return {"message": "Pitch logged", "pitcher_stats": stats}


@router.get("/game-mode/{game_id}/scoreboard")
def get_scoreboard(game_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    session = _load_session(db, game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Game session not found.")

    return {
        "game_id": game_id,
        "home_team": session["home_team"],
        "away_team": session["away_team"],
        "game_date": session["game_date"],
        "inning": session["inning"],
        "score": session["score"],
        "player_stats": list(session["player_stats"].values()),
        "pitcher_stats": list(session["pitcher_stats"].values()),
        "saved": session["saved"],
    }


@router.post("/game-mode/{game_id}/save")
def save_game(game_id: str, db: Session = Depends(get_db)) -> Dict[str, Any]:
    session = _load_session(db, game_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Game session not found.")
    if session["saved"]:
        raise HTTPException(status_code=400, detail="Game already saved.")

    try:
        game_date_parsed = date.fromisoformat(session["game_date"])
    except ValueError:
        game_date_parsed = date.today()

    records_saved = 0

    for key, pstats in session["player_stats"].items():
        player_name = pstats["player_name"]
        team = pstats["team"]
        opponent = session["away_team"] if team == session["home_team"] else session["home_team"]

        existing = db.query(GameRecord).filter(GameRecord.player_name == player_name).first()
        player_id = existing.player_id if existing else db.query(GameRecord).count() + records_saved + 1

        record = GameRecord(
            player_id=player_id,
            player_name=player_name,
            team=team,
            position="IF",
            game_date=game_date_parsed,
            at_bats=pstats.get("at_bats", 0),
            hits=pstats.get("hits", 0),
            doubles=pstats.get("doubles", 0),
            triples=pstats.get("triples", 0),
            home_runs=pstats.get("home_runs", 0),
            runs=pstats.get("runs", 0),
            rbi=pstats.get("rbi", 0),
            strikeouts=pstats.get("strikeouts", 0),
            walks=pstats.get("walks", 0),
            hit_by_pitch=0,
            innings_pitched=0.0,
            earned_runs=0,
            hits_allowed=0,
            walks_allowed=0,
            strikeouts_pitched=0,
            game_id=game_id,
            opponent=opponent,
        )
        db.add(record)
        records_saved += 1

    for key, pstats in session["pitcher_stats"].items():
        pitcher_name = pstats["pitcher_name"]
        team = pstats["team"]
        opponent = session["away_team"] if team == session["home_team"] else session["home_team"]

        existing = db.query(GameRecord).filter(GameRecord.player_name == pitcher_name).first()
        player_id = existing.player_id if existing else db.query(GameRecord).count() + records_saved + 1

        record = GameRecord(
            player_id=player_id,
            player_name=pitcher_name,
            team=team,
            position="P",
            game_date=game_date_parsed,
            at_bats=0,
            hits=0,
            doubles=0,
            triples=0,
            home_runs=0,
            runs=0,
            rbi=0,
            strikeouts=0,
            walks=0,
            hit_by_pitch=0,
            innings_pitched=pstats.get("innings_pitched", 0.0),
            earned_runs=pstats.get("earned_runs", 0),
            hits_allowed=pstats.get("hits_allowed", 0),
            walks_allowed=pstats.get("walks_allowed", 0),
            strikeouts_pitched=pstats.get("strikeouts_pitched", 0),
            game_id=game_id,
            opponent=opponent,
        )
        db.add(record)
        records_saved += 1

    db.commit()
    session["saved"] = True
    _persist_session(db, session)

    return {"success": True, "message": f"Saved {records_saved} records.", "records_saved": records_saved}
