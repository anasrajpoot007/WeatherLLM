CREATE TABLE leagues (
    idLeague INTEGER PRIMARY KEY,
    name TEXT
);

CREATE TABLE venues (
    idVenue INTEGER PRIMARY KEY,
    name TEXT
);

CREATE TABLE teams (
    idTeam INTEGER PRIMARY KEY,
    name TEXT,
    idLeague INTEGER,
    idVenue INTEGER,
    FOREIGN KEY (idLeague) REFERENCES leagues(idLeague),
    FOREIGN KEY (idVenue) REFERENCES venues(idVenue)
);


CREATE TABLE players (
    idPlayer INTEGER PRIMARY KEY,
    name TEXT,
    idTeam INTEGER,
    FOREIGN KEY (idTeam) REFERENCES teams(idTeam)
);

CREATE TABLE events (
    idEvent INTEGER PRIMARY KEY,
    name TEXT,
    idLeague INTEGER,
    idHomeTeam INTEGER,
    idAwayTeam INTEGER,
    idVenue INTEGER,
    FOREIGN KEY (idLeague) REFERENCES leagues(idLeague),
    FOREIGN KEY (idHomeTeam) REFERENCES teams(idTeam),
    FOREIGN KEY (idAwayTeam) REFERENCES teams(idTeam),
    FOREIGN KEY (idVenue) REFERENCES venues(idVenue)
);