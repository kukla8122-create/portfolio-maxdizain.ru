'use strict';
module.exports=(req,res)=>{
 res.setHeader('Cache-Control','no-store');
 if(req.method!=='GET'){res.setHeader('Allow','GET');return res.status(405).json({error:'Метод не поддерживается.'})}
 // Public OAuth identifier; this is not a client secret.
 const clientId=process.env.GOOGLE_CALENDAR_CLIENT_ID||'553790601767-5qb7tlrlc5imgav86i8bkamdu0jul93g.apps.googleusercontent.com';
 return res.status(200).json({configured:!!clientId,clientId});
};
