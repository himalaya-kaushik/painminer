# painminer

Personal, read-only research tool. Single user, non-commercial.

## What it does
Reads public posts and comments, extracts a one-line
summary of the problem being described, and stores that
summary with a permalink. Used by me to keep track of
recurring workflow problems people mention publicly.

## Reddit usage
- Read-only. No posting, commenting, voting, or moderating.
- Fixed set of search phrases across a fixed subreddit list.
- A few hundred queries/day, far below 100 QPM.
- Raw post content is deleted after processing; only a
  generated summary and the permalink are retained.
- No redistribution, resale, or model training on Reddit data.

## Other sources
Hacker News, GitHub Issues, public software review sites.

## Stack
Python, PRAW, local LLM via LM Studio, Postgres.
