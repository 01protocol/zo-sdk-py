# Changelog
All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

- Add `Zo.cancel_all_orders{,_ix}`.
- BREAKING: Add heimdall to `Zo.withdraw` accounts list.
- BREAKING: Change default commitment from `confirmed` to `finalized`.

## [0.1.4] - 2022-08-02

- Add `max_ts` parameter to `Zo.place_order`.
- Add `Zo.send` method to dispatch custom instructions.
- Add `_ix` variants for `Zo` methods.
- Add `Zo.close_position`.
- Add `Zo.refresh_orders`.
- Omit `LUNA-PERP` market.
- DEPRECATE: Deprecate `PositionInfo.value` in favour of `PositionInfo.entry_value`.

## [0.1.3] - 2022-06-10

- DEPRECATE: Deprecate `MarketInfo.funding_rate` in favour of `MarketInfo.funding_{info,sample_start_time}` for new funding formula.

## [0.1.2] - 2022-04-08

- Add `MarketInfo.{mark,index}_{price,twap}` and `MarketInfo.funding_rate`.
- Add `commitment` parameter to `Zo.refresh()`.
- Fix `Zo.orderbook` missing when `load_margin=False`.

## [0.1.1] - 2022-03-08

- Fix broken `Zo.place_order`.

## [0.1.0] - 2022-03-06

- Initial release.
