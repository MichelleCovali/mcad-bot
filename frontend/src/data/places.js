// Local navigation targets only. These places never create incident records.
const places = [
  {
    id: "europe",
    label: "Europe",
    lat: 52.5,
    lng: 13.5,
    zoom: 4,
    keywords: ["Европа"],
  },
  {
    id: "netherlands",
    label: "Netherlands",
    lat: 52.2,
    lng: 5.4,
    zoom: 7,
    keywords: ["Nederland", "Holland", "Нидерланды"],
  },
  {
    id: "rotterdam",
    label: "Rotterdam, Netherlands",
    lat: 51.9244,
    lng: 4.4777,
    zoom: 11,
    keywords: ["Роттердам"],
  },
  {
    id: "amsterdam",
    label: "Amsterdam, Netherlands",
    lat: 52.3676,
    lng: 4.9041,
    zoom: 11,
    keywords: ["Амстердам"],
  },
  {
    id: "belgium",
    label: "Belgium",
    lat: 50.75,
    lng: 4.5,
    zoom: 7,
    keywords: ["België", "Belgique", "Бельгия"],
  },
  {
    id: "antwerp",
    label: "Antwerp, Belgium",
    lat: 51.2194,
    lng: 4.4025,
    zoom: 11,
    keywords: ["Antwerpen", "Антверпен"],
  },
  {
    id: "germany",
    label: "Germany",
    lat: 51.1657,
    lng: 10.4515,
    zoom: 6,
    keywords: ["Deutschland", "Германия"],
  },
  {
    id: "hamburg",
    label: "Hamburg, Germany",
    lat: 53.5511,
    lng: 9.9937,
    zoom: 10,
    keywords: ["Гамбург"],
  },
  {
    id: "united-kingdom",
    label: "United Kingdom",
    lat: 54.6,
    lng: -2.8,
    zoom: 6,
    keywords: ["UK", "Britain", "Великобритания"],
  },
  {
    id: "london",
    label: "London, United Kingdom",
    lat: 51.5074,
    lng: -0.1278,
    zoom: 10,
    keywords: ["Лондон"],
  },
  {
    id: "france",
    label: "France",
    lat: 46.6,
    lng: 2.4,
    zoom: 6,
    keywords: ["Франция"],
  },
  {
    id: "le-havre",
    label: "Le Havre, France",
    lat: 49.4944,
    lng: 0.1079,
    zoom: 11,
    keywords: ["Гавр"],
  },
  {
    id: "spain",
    label: "Spain",
    lat: 40.4,
    lng: -3.7,
    zoom: 6,
    keywords: ["España", "Испания"],
  },
  {
    id: "barcelona",
    label: "Barcelona, Spain",
    lat: 41.3874,
    lng: 2.1686,
    zoom: 10,
    keywords: ["Барселона"],
  },
  {
    id: "italy",
    label: "Italy",
    lat: 42.5,
    lng: 12.5,
    zoom: 6,
    keywords: ["Italia", "Италия"],
  },
  {
    id: "norway",
    label: "Norway",
    lat: 61.5,
    lng: 8.5,
    zoom: 5,
    keywords: ["Norge", "Норвегия"],
  },
  {
    id: "denmark",
    label: "Denmark",
    lat: 56.2,
    lng: 9.5,
    zoom: 7,
    keywords: ["Danmark", "Дания"],
  },
  {
    id: "sweden",
    label: "Sweden",
    lat: 62,
    lng: 15,
    zoom: 5,
    keywords: ["Sverige", "Швеция"],
  },
  {
    id: "poland",
    label: "Poland",
    lat: 52,
    lng: 19.1,
    zoom: 6,
    keywords: ["Polska", "Польша"],
  },
  {
    id: "greece",
    label: "Greece",
    lat: 39,
    lng: 22.5,
    zoom: 6,
    keywords: ["Ελλάδα", "Греция"],
  },
].map((place) =>
  Object.freeze({ ...place, keywords: Object.freeze(place.keywords ?? []) }),
);

const normalize = (value) =>
  String(value ?? "")
    .normalize("NFKD")
    .replace(/\p{Diacritic}/gu, "")
    .toLowerCase()
    .trim();

/** Search a small bundled gazetteer; no requests leave the browser while typing. */
export function searchPlaces(query) {
  const search = normalize(query);
  if (!search) return [];

  const searchable = places.map((place) => ({
    place,
    names: [place.label, ...place.keywords].map(normalize),
  }));

  return searchable
    .filter(({ names }) => names.some((name) => name.includes(search)))
    .sort((a, b) => {
      const priority = ({ names }) =>
        names.some((name) => name === search)
          ? 0
          : names.some((name) => name.startsWith(search))
            ? 1
            : 2;
      return priority(a) - priority(b);
    })
    .slice(0, 6)
    .map(({ place }) => place);
}
