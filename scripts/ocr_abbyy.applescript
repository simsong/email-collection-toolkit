-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

-- Run only the explicit recognition/export command for a copied input PDF.
-- The Apple event has a failure deadline, rather than a periodic busy check.
-- A returned command must also report idle before its output is consumed.
-- Older ABBYY versions that return while busy fail with a clear explanation.
-- No elapsed delay is accepted as proof that an external export completed.
-- The research caller reports this failure and never changes source mail.

on run arguments
    set inputFile to POSIX file (item 1 of arguments)
    set outputFile to POSIX file (item 2 of arguments)
    tell application "ABBYY FineReader for ScanSnap"
        with timeout of 3600 seconds
            recognize inputFile and export to outputFile as pdf silent mode true
        end timeout
        if busy then error "ABBYY returned before export completed and exposes no completion notification; asynchronous export is unsupported."
    end tell
end run
