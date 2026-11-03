// @ts-check
// Shapes of the data the server sends. Types only; nothing here runs.

/**
 * @typedef {object} Episode
 * @property {string} CmdURL
 * @property {string} [ProgramID]
 * @property {string} [Title]
 * @property {string} [EpisodeTitle]
 * @property {string} [EpisodeNumber]
 * @property {string} [Synopsis]
 * @property {number} [StartTime]        seconds since 1970
 * @property {number} [EndTime]
 * @property {number} [RecordStartTime]
 * @property {number} [RecordEndTime]
 * @property {boolean | null} Watched     null when the DVR doesn't report it
 * @property {number | null} ResumeSeconds
 */

/**
 * @typedef {object} Show
 * @property {string} SeriesID
 * @property {string} Title
 * @property {string | null} ImageURL
 * @property {Episode[]} Episodes
 * @property {boolean} [Scheduled]       has a recording rule but no recordings yet
 * @property {string} [Error]            the show's recordings couldn't be loaded
 */

/**
 * @typedef {object} Rule
 * @property {number | null} keep
 * @property {boolean} delete_watched
 */

/**
 * @typedef {object} PlanEpisode
 * @property {string} CmdURL
 * @property {string} [EpisodeTitle]
 * @property {string} [EpisodeNumber]
 * @property {number} [StartTime]
 * @property {string} [Reason]
 */

/**
 * @typedef {object} PlanGroup
 * @property {string} SeriesID
 * @property {string} Title
 * @property {Rule} Rule
 * @property {PlanEpisode[]} Episodes
 */

/**
 * @typedef {object} AutoInfo
 * @property {boolean} enabled
 * @property {string} time
 * @property {string} tz
 * @property {number} max
 * @property {string | null} next
 * @property {{when: string, summary: string} | null} last
 */

/**
 * @typedef {object} HistoryEntry
 * @property {string} when
 * @property {"manual" | "keep-rules" | "auto"} source
 * @property {string} [show]
 * @property {string} [episode]
 * @property {string} [number]
 * @property {number} [start]
 * @property {boolean} rerecord
 * @property {boolean} ok
 * @property {string} [error]
 */

export {};
