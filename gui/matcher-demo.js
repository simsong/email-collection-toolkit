/* Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. */
"use strict";

// Deliberately fictional .test addresses. No catalog or network connection.
const sharedMessage = new MatcherMessage("shared-sam", "2010-06-15");
function syntheticAddress(id, email, firstUse, lastUse, count) {
  const start = Date.parse(firstUse);
  const days = (Date.parse(lastUse) - start) / 86400000;
  const observations = Array.from({length: count}, (_, index) => new MatcherMessage(
    `${id}-${index}`, new Date(start + Math.floor(days * index / Math.max(count - 1, 1)) * 86400000).toISOString().slice(0, 10)));
  if (id === "sam-work" || id === "sam-home") observations[1] = sharedMessage;
  return new MatcherAddress(id, email, observations);
}

const demoGroups = [
  new MatcherGroup("sam", "Sam L. Green", [
    syntheticAddress("sam-work", "sam.green@northstar.test", "2008-04-12", "2026-08-30", 1842),
    syntheticAddress("sam-slg", "slg@cedar.test", "1996-02-18", "2014-11-06", 763),
    syntheticAddress("sam-home", "sam@green-family.test", "2003-07-09", "2026-09-01", 426),
  ]),
  new MatcherGroup("slg", "S. L. Green", [syntheticAddress("slg-lab", "slg@harbor-lab.test", "2012-01-16", "2025-12-19", 318)]),
  new MatcherGroup("alex", "Alex Morgan", [
    syntheticAddress("alex-work", "alex.morgan@northstar.test", "2016-03-22", "2026-08-29", 924),
    syntheticAddress("alex-home", "amorgan@postbox.test", "2010-10-03", "2024-05-17", 207),
  ]),
  new MatcherGroup("alex-old", "A. Morgan", [syntheticAddress("alex-lab", "amorgan@harbor-lab.test", "2011-06-02", "2016-02-12", 186)]),
  new MatcherGroup("priya", "Priya Shah", [
    syntheticAddress("priya-work", "priya@slg-research.test", "2018-09-04", "2026-08-14", 612),
    syntheticAddress("priya-home", "p.shah@postbox.test", "2017-01-11", "2026-06-22", 139),
  ]),
  new MatcherGroup("jordan", "Jordan Lee", [syntheticAddress("jordan-work", "jordan.lee@cedar.test", "2020-04-07", "2026-07-30", 453)]),
  new MatcherGroup("morgan", "Morgan Reed", [syntheticAddress("morgan-work", "m.reed@northstar.test", "2005-11-21", "2025-10-08", 1287)]),
  new MatcherGroup("elena", "Elena Rossi", [syntheticAddress("elena-work", "elena@harbor-lab.test", "2014-08-13", "2026-08-21", 538)]),
];

async function demoMatcher(model) {
  const moves = [];
  for (const [addressId, targetId] of [["slg-lab", "sam"], ["alex-lab", "alex"]]) {
    const source = model.groups.find(group => group.addresses.some(address => address.id === addressId));
    if (source && source.id !== targetId && model.find(targetId)) {
      moves.push(new MatcherMove(new MatcherSelection(source.id, addressId), targetId));
    }
  }
  return moves;
}

const allAddresses = demoGroups.flatMap(group => group.addresses);
const institutionGroups = [
  new MatcherGroup("northstar", "Northstar Institute", allAddresses.filter(address => address.email.endsWith("@northstar.test"))),
  new MatcherGroup("harbor", "Harbor Laboratory", allAddresses.filter(address => address.email.endsWith("@harbor-lab.test") && address.id !== "alex-lab")),
  new MatcherGroup("harbor-old", "Harbor Lab", allAddresses.filter(address => address.id === "alex-lab")),
  new MatcherGroup("cedar", "Cedar University", allAddresses.filter(address => address.email.endsWith("@cedar.test"))),
  new MatcherGroup("slg-research", "SLG Research", allAddresses.filter(address => address.email.endsWith("@slg-research.test"))),
  new MatcherGroup("unassigned", "Unassigned / personal", allAddresses.filter(address => /@(postbox|green-family)\.test$/.test(address.email))),
];

async function institutionDemoMatcher(model) {
  return model.find("harbor-old") && model.find("harbor")
    ? [new MatcherMove(new MatcherSelection("harbor-old"), "harbor")] : [];
}

const institutionMode = new URLSearchParams(location.search).get("kind") === "institution";
const matcherWindow = institutionMode
  ? new InstitutionMatcherWindow(document.getElementById("matcher"), institutionGroups, institutionDemoMatcher)
  : new NameMatcherWindow(document.getElementById("matcher"), demoGroups, demoMatcher);
