# PRODUCT.md

This document defines the product's purpose, direction and guiding principles.
Supporting documents define the details without changing the direction set
here:

- [Implemented milestone and setup guide](docs/SETUP.md)
- [Product specification](docs/SPECIFICATION.md)
- [Architecture](docs/ARCHITECTURE.md)
- [External integrations](docs/INTEGRATIONS.md)
- [Runtime environments and configuration](docs/ENVIRONMENT.md)
- [Production deployment and operations](docs/PRODUCTION.md)

## Purpose

This application is a private management tool for our Old School RuneScape community. "Escape" is currently only a placeholder name.

The application should help automate recurring community tasks while keeping community managers in control.

Discord will be the main interface and Wise Old Man will be used for OSRS statistics and competitions.

The application will be written in **Python** and developed to run using **Docker**.

## Members & Ranks

The application should maintain our clan members and link their Discord and OSRS accounts.

It should support clan rank assignment based on configurable rules such as membership duration, activity and participation.

Rank rules will evolve over time, so they should be extendable.

Managers must always be able to manually change or override ranks.

Clan ranks and the monthly activity leaderboard are separate concepts.

## Activity & Leaderboard

The application should track OSRS activity using available Wise Old Man data and maintain a monthly clan leaderboard.

The ranking system should eventually consider more than raw XP, including factors such as activity, membership duration, consistency and community participation.

The exact scoring system will be developed later.

Members should be able to view relevant information through Discord commands such as:

* `/stats`
* `/progress`
* `/rank`
* `/leaderboard`
* `/member`
* `/competition`

Management commands should also exist for managing members, ranks, events and application settings.

## Skill of the Week & Boss of the Week

The application should automate **Skill of the Week** and **Boss of the Week** competitions through Wise Old Man.

When a new event is scheduled, the application creates a poll in the appropriate Discord channel.

A configurable number of eligible skills or bosses are randomly selected and presented as voting options.

The option receiving the most votes becomes the next event.

If multiple options are tied for the highest number of votes, the application randomly selects the winner **from only those tied options**.

The winning skill or boss is then used to create the corresponding Wise Old Man competition.

## Event Rotation

Managers control which skills and bosses are eligible for selection.

Recently used options should be excluded from new polls for a configurable period.

For **Boss of the Week**, the default cooldown before the same boss can appear again should be **12 months**.

The cooldown and number of poll options must be configurable.

Managers should also be able to manually intervene, create events, cancel events or override the automated process when necessary.

## Management

Community managers should retain control over the application.

Important behavior should be configurable rather than hard-coded, including:

* Community settings
* Discord channels and roles
* Member ranks and rank rules
* Ranking rules
* Event schedules
* Voting duration
* Number of poll options
* Eligible skills and bosses
* Event cooldowns

Where reasonable, management should be possible directly through Discord.

## Development Direction

Keep the application simple and focused on our own community.

Use **Python** as the application language and **Docker** as the intended runtime.

Prefer configurable and extendable systems over hard-coded rules, but avoid building functionality we do not currently need.

Automation should reduce management work without taking control away from community managers.
