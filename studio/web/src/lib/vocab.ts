// What a column's name says it is: a place, a person's trait, or one of the cuts a business usually makes.
// Read from ordinary words in the model's own column names, so any model's columns sort the same way.

export const PLACE = /store|city|region|country|state|location|branch|site|territor|market|area|zone/i;
export const PEOPLE = /gender|\bsex\b|^age|age ?(band|group|range)|income|marital|education|occupation|generation/i;
/** What a business is usually cut by first, when the question names nothing. */
export const BUSINESS = /category|segment|product|brand|store|region|channel|promotion|type|setting|line of business|\blob\b|plan|facility|specialty|provider|drug|network|funding|condition|episode|\bmdc\b|\bdrg\b/i;
